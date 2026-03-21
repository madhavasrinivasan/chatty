# Chatty Backend — Project Summary

This document describes the **Chatty** backend in detail: purpose, architecture, major components, data concepts, and **every HTTP route** with what it does.  
**LightRAG** is intentionally summarized only as “optional RAG layer used in some flows” — not deep internals.

---

## 1. What this project is

**Chatty** is a **FastAPI** backend for an **AI-powered e-commerce chatbot** that:

- Connects **Shopify** stores via OAuth and Admin APIs.
- Stores **products, pages, policies, and collections** in PostgreSQL (`store_knowledge`) with **embeddings** (Google Gemini) and **full-text search** (PostgreSQL `tsvector`).
- Runs an **AI orchestrator** that classifies user intent (orders, returns, general chat, product search, etc.), expands queries, and returns structured JSON for the frontend.
- Supports **merchant admin** flows (auth, knowledge upload, Shopify install, sync status, live chat session listing).
- Supports **storefront/chatbot** flows via **encrypted API key** (`x-api-key`) for the main chat and session sync.
- Uses a **background worker** (`worker.py`) to process queued jobs (e.g. full product ingest, vector creation, store DNA refresh).

---

## 2. Technology stack

| Layer | Technology |
|--------|------------|
| Web framework | **FastAPI** (`main.py`, routers under `app/admin/routes/`) |
| Database ORM | **Tortoise ORM** (async PostgreSQL) — `app/core/models/models.py`, `init_db()` in `app/core/config/db.py` |
| Database | **PostgreSQL** with **pgvector** and **tsvector** for hybrid retrieval |
| AI / embeddings | **Google GenAI** (`google.genai`) — Gemini models for chat, routing, synthesis, embeddings |
| Shopify | REST/GraphQL via **Shopify Python API** + **httpx** where needed |
| Auth | **JWT** (users), **AES-encrypted JWT** stored as chatbot `api_key` for storefront |
| Background work | **worker.py** polling `background_tasks` table; FastAPI `BackgroundTasks` for small async follow-ups |
| Config | **Pydantic Settings** — `app/core/config/config.py`, `.env` |

---

## 3. Repository layout (high level)

```
chatty_backend/
├── main.py                 # FastAPI app, lifespan, CORS, health, a few root routes
├── worker.py               # Polls DB for pending background_tasks; runs ingestion / vectors / DNA
├── pyproject.toml / uv.lock
├── db_migration_*.sql      # Manual SQL migrations when not using dev auto-schema
├── app/
│   ├── admin/
│   │   ├── controller/
│   │   │   ├── appcontroller.py   # Main business logic: Shopify, orchestrator, RAG query, uploads
│   │   │   └── authcontroller.py  # Register, login, logout
│   │   └── routes/
│   │       ├── index.py           # Mounts /admin/* sub-routers + sessions/sync endpoints
│   │       ├── approutes.py       # /admin/app/* (user, upload, orchestrate, chatbot, Shopify)
│   │       ├── authroutes.py      # /admin/auth/*
│   │       └── sessionroutes.py   # /admin/session/*
│   └── core/
│       ├── config/
│       │   ├── config.py          # Settings (env)
│       │   └── db.py              # Tortoise init, pgvector/tsvector upgrade hook, LightRAG factory (not detailed here)
│       ├── models/
│       │   ├── models.py          # Tortoise models (users, ecom_store, store_knowledge, chat sessions, etc.)
│       │   └── dbontrollers/
│       │       └── admindbcontroller.py  # DB access layer
│       ├── schema/
│       │   ├── schema.py          # Pydantic request/response models, orchestrator schemas
│       │   ├── schemarespone.py   # Generic APIResponse wrapper
│       │   ├── applicationerror.py
│       │   └── errorschema.py     # Exception handler registration
│       └── services/
│           ├── ai_orchestrator.py      # Intent routing + query expansion
│           ├── response_synthesis.py   # Final LLM answer + product cards
│           ├── database_executor.py    # Hybrid search over store_knowledge
│           ├── shopify_service.py      # Discounts, install URL, product helpers, etc.
│           ├── shopify_return_service.py
│           ├── session_memory.py       # User facts, transcripts, order history for context
│           ├── webcrawler.py           # Crawl, PDF, embeddings batch
│           ├── filehandler.py
│           └── jwt.py
└── .cursor/                # Cursor rules / agent notes
```

---

## 4. Application lifecycle (`main.py`)

- **`lifespan`**: On startup calls `init_db()` (Tortoise + dev schema generation); on shutdown `close_db()`.
- **`app.state.rags`**: Placeholder dict for optional per-store RAG instances (LightRAG not expanded here).
- **Duplicate `startup` / `shutdown` events**: Also call `init_db()` / `close_db()` — effectively DB is initialized twice on startup in current code; worth consolidating in production.
- **CORS**: `allow_origins=["*"]`, all methods/headers.
- **Exception handling**: `register_exception_handlers(app)` maps `ApplicationError` to JSON responses.

---

## 5. Authentication patterns

### Admin / dashboard (merchant)

- Header: **`adminauthtoken`** — validated via `AppController.validate_user` → looks up `user_sessions`, loads `users`.

### Storefront / embedded chatbot

- Header: **`x-api-key`** (or `chatty-api-key`) — `AppController.validate_chatbot_api_key` decrypts AES payload, decodes JWT → `{ user_id, chatbot_id }`.

---

## 6. Core domain concepts (non–LightRAG)

### `ecom_store`

Links a **user** to a **Shopify shop** (`store_name`, tokens, `chatbot_id`, optional `store_dna`, sync fields like `last_synced_at`, `sync_status`).

### `store_knowledge`

Central table for **products** and **non-product** content (pages, policies, collections-as-rows). Used by **hybrid search** (vector + keyword) in `database_executor.py`.

### `background_tasks`

Queued jobs: `create_vectors`, `get_products`, `get_orders`, `query_expander_context`. Worker picks `pending` tasks and runs the appropriate handler in `AppController` or `Services`.

### Chat persistence

- **`ChatSession` / `ChatMessage`**: Normalized chat for storefront flows (UUID session, messages with roles).
- **`ChatTranscript`**: JSON transcript used by dual-memory / sync flows.
- **`UserMemorySummary`**: Extracted long-term facts per email + store.

### AI orchestrator (`ai_orchestrator.py`)

Routes messages to intents such as `ORDER_SUPPORT`, `GENERAL_CHAT`, `HYBRID_SEARCH`, `RETURN_REQUEST`, etc., and can fetch **active discounts** when the router sets `wants_discounts`. Results feed `AppController.process_orchestrator_query` and optionally `response_synthesis.py`.

---

## 7. Complete HTTP route catalog

Assume the server base URL is `http://<host>:<port>` (default in `main.py` is port **3009** when run directly).  
All paths below are **exact** FastAPI paths as registered.

---

### 7.1 Root app (`main.py`) — no `/admin` prefix

| Method | Path | Auth | What it does |
|--------|------|------|----------------|
| **GET** | `/health` | None | Liveness check. Returns `{"status": "healthy"}`. |
| **GET** | `/api/auth/oauth/callback` | None (Shopify redirect) | **Defined twice** in `main.py` (duplicate path). Both delegate to `AppController.shopify_callback(request)`: OAuth code exchange, HMAC validation, token save on `ecom_store`, enqueue background tasks. In practice the **last** registration wins. |
| **POST** | `/get_answer` | None (debug-style) | Logs body/headers (`chatty-api-key`, `chatty-shop-url`, `chatty-customer-email`) and returns a static success message. Placeholder / testing endpoint. |

---

### 7.2 Admin API — prefix `/admin` (`approuter` in `app/admin/routes/index.py`)

The main admin router includes three sub-routers and adds four routes directly.

#### 7.2.1 Auth — prefix `/admin/auth` (`authroutes.py`)

| Method | Path | Auth | What it does |
|--------|------|------|----------------|
| **POST** | `/admin/auth/register` | None | `AuthController.register_user` — creates user (and related session handling per controller). |
| **POST** | `/admin/auth/login` | None | `AuthController.login_user` — validates credentials, creates session token; uses client IP from `X-Forwarded-For` or direct client. |
| **POST** | `/admin/auth/logout` | None | `AuthController.logout_user` — invalidates session for IP-based flow. |

#### 7.2.2 App — prefix `/admin/app` (`approutes.py`)

| Method | Path | Auth | What it does |
|--------|------|------|----------------|
| **GET** | `/admin/app/user` | `adminauthtoken` | Returns current user dict from `validate_user`. |
| **POST** | `/admin/app/uploadknowlegdebase` | `adminauthtoken` | Multipart: uploads files (optional), `name`, optional `urls`. Creates chatbot asset records and enqueues **`create_vectors`** background task via `AppController.upload_knowledge_base`. |
| **POST** | `/admin/app/response` | `adminauthtoken` | Body: `llmrequest` (question, optional `store_id`, mode). Runs LightRAG-style query via `AppController.get_response` (Gemini + configured store workspace — RAG details omitted here). |
| **POST** | `/admin/app/orchestrate` | `adminauthtoken` | Body: `OrchestratorRequest`. Full AI orchestrator pipeline **without** storefront chat session DB persistence. Returns orchestrator result (route, payloads, `final_response` when synthesized). |
| **POST** | `/admin/app/orchestrate/chatbot` | **`x-api-key`** (chatbot) | Main **storefront chat**. Resolves/creates `ChatSession`, saves user message, loads last 10 messages from DB, optionally loads user facts / previous session / Shopify order history from headers, calls `process_orchestrator_query`, saves assistant reply to `ChatMessage`. |
| **POST** | `/admin/app/chat` | **`x-api-key`** | **Alias** — same implementation as `/orchestrate/chatbot`. |
| **GET** | `/admin/app/shopify-callback` | None (Shopify redirect query params) | Same as OAuth callback handler: `AppController.shopify_callback`. |
| **POST** | `/admin/app/addshoppify` | `adminauthtoken` | Body: `AddshopifyRequest` (`store_name`). Creates chatbot + `ecom_store`, encrypts API key, returns Shopify **install URL** for OAuth. |

#### 7.2.3 Session sync — prefix `/admin/session` (`sessionroutes.py`)

| Method | Path | Auth | What it does |
|--------|------|------|----------------|
| **POST** | `/admin/session/sync` | **`x-api-key`** | Body: `SyncSessionRequest` (session_id, user_email, chat_history). Resolves `ecom_store` from chatbot id, queues `process_and_save_session` in `BackgroundTasks` to upsert `ChatTranscript` and optionally extract user facts. Returns JSON `status: success`. |

#### 7.2.4 Direct routes on `approuter` (`index.py`)

| Method | Path | Auth | What it does |
|--------|------|------|----------------|
| **GET** | `/admin/sessions` | `adminauthtoken` | `AppController.list_chat_sessions` — lists **active** `ChatSession` rows for the merchant’s shop, latest message snippet, ordered by `updated_at` DESC. |
| **GET** | `/admin/sessions/{session_id}/messages` | `adminauthtoken` | `AppController.list_chat_session_messages` — full chronological `ChatMessage` list for that session (scoped to merchant’s shop domain). |
| **GET** | `/admin/sync-status` | `adminauthtoken` | `AppController.get_sync_status` — returns `last_synced_at` and `sync_status` from `ecom_store` for the user’s store. |
| **POST** | `/admin/sync-trigger` | `adminauthtoken` | `AppController.trigger_sync` — sets sync state to **syncing**, enqueues **`get_products`** background task (worker runs full Shopify ingest + related indexing). |

---

## 8. Background worker (`worker.py`)

- Polls **`background_tasks`** for **`pending`** jobs (note: current implementation may fetch one batch at a time — see code).
- Dispatches by `task_type`:
  - **`create_vectors`**: crawl/PDF → embeddings → optional LightRAG insert path.
  - **`get_products`**: `AppController.get_products_background_task` — Shopify session, products, collections, pages/policies, DB inserts, metafield for API key, updates task status and store sync fields when configured.
  - **`query_expander_context`**: store DNA generation from titles (`Services.generate_store_dna_from_titles`).

---

## 9. Configuration (`app/core/config/config.py` + `.env`)

Typical variables include: `DB_URL`, JWT and AES secrets, `GEMINI_*`, Shopify keys and callback domain, file upload paths, rate limits.  
See `README.md` for an example `.env` block.

---

## 10. Error handling

- **`ApplicationError`** subclasses carry `status_code`; `errorschema.py` registers a handler returning JSON with `type`, `message`, etc.
- Many controllers return **`APIResponse`** with `status`, `message`, `data` (some call sites still pass `success=` for compatibility — verify client expectations).

---

## 11. SQL migrations

- Example: `db_migration_chat_sessions.sql` — adds `chat_sessions`, `chat_messages`, and `ecom_store` sync columns when not relying on dev `generate_schemas`.

---

## 12. Quick reference — all routes (flat list)

**Root**

- `GET /health`
- `GET /api/auth/oauth/callback` (duplicate handler in `main.py`)
- `POST /get_answer`

**Admin**

- `POST /admin/auth/register`
- `POST /admin/auth/login`
- `POST /admin/auth/logout`
- `GET /admin/app/user`
- `POST /admin/app/uploadknowlegdebase`
- `POST /admin/app/response`
- `POST /admin/app/orchestrate`
- `POST /admin/app/orchestrate/chatbot`
- `POST /admin/app/chat`
- `GET /admin/app/shopify-callback`
- `POST /admin/app/addshoppify`
- `POST /admin/session/sync`
- `GET /admin/sessions`
- `GET /admin/sessions/{session_id}/messages`
- `GET /admin/sync-status`
- `POST /admin/sync-trigger`

---

*Generated for the Chatty backend codebase. For LightRAG-specific storage and graph behavior, see `app/core/config/db.py` and service modules that call `initialize_light_rag`.*
