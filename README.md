# Chatty Backend

Backend for **Chatty**, an AI-powered e-commerce chatbot platform. It connects to Shopify stores, ingests products and content, and serves an AI orchestrator that routes user intents (order support, general chat, product/search, policies) and runs hybrid search over store knowledge (PostgreSQL + pgvector + full-text search).

## Features

- **Shopify integration**: OAuth (code flow), product/collection/page/policy ingestion, order status via GraphQL, shop metafield for chatbot API key
- **Store knowledge**: Single-table design (`store_knowledge`) for products, pages, policies, and collections with vector embeddings (Gemini) and `tsvector` full-text search
- **AI orchestration**: Intent routing (ORDER_SUPPORT, GENERAL_CHAT, HYBRID_SEARCH, GRAPH_SEARCH, PARALLEL_SEARCH) and query expansion using store DNA; subscription-tier rules (e.g. starter vs enterprise)
- **Order support**: When the user provides an order number, fetches order status from Shopify GraphQL; otherwise returns a “need orderId” prompting payload for the client
- **Background worker**: Async worker for `get_products` (full catalog + pages + policies + collections + shop metafield) and `query_expander_context` (store DNA generation)
- **Auth**: JWT-based auth; API key (encrypted) per chatbot for storefront/API use

## Prerequisites

- **Python 3.12+**
- **PostgreSQL** (with [pgvector](https://github.com/pgvector/pgvector) extension for vector search)
- **[uv](https://github.com/astral-sh/uv)** (recommended) or pip

## Setup

### 1. Install dependencies

```bash
uv sync
```

Or with pip:

```bash
pip install -e .
```

### 2. Environment configuration

Create a `.env` file in the project root. Required and optional variables:

```env
# Database (required)
DB_URL=postgresql://user:password@localhost:5432/chatty_db

# JWT (required)
JWT_SECRET=your-secret-key
JWT_ALGORITHM=HS256

# App
APP_NAME=chatty
ENV=development
DEBUG=false

# API key header name
API_KEY_HEADER=x-api-key

# AES (for encrypting chatbot API keys / tokens)
SECRET_KEY=your-32-byte-aes-secret-key!!

# File upload
FILE_UPLOAD_DIRECTORY_PDF=Assets/PDF
FILE_UPLOAD_MAX_SIZE=5242880

# Rate limiting
RATE_LIMIT_PER_MINUTE=60

# Google GenAI (required for embeddings, store DNA, orchestrator)
GEMINI_API_KEY=your-gemini-api-key

# Shopify (required for OAuth and Admin API)
SHOPIFY_API_KEY=your-shopify-api-key
SHOPIFY_API_SECRET=your-shopify-api-secret
SHOPIFY_API_VERSION=2024-01
SHOPIFY_CALLBACK_DOMAIN=https://your-app-domain.com

# LightRAG (optional)
LIGHTRAG_KV_STORAGE=PGKVStorage
LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage
LIGHTRAG_GRAPH_STORAGE=PGGraphStorage
LIGHTRAG_VECTOR_STORAGE=PGVectorStorage
```

### 3. Database

Ensure PostgreSQL is running and the database exists. In development, the app runs schema init and optional vector/tsvector upgrades on `store_knowledge` at startup.

### 4. Background worker

Run the worker in a separate process so background tasks (product ingestion, store DNA) are processed:

```bash
uv run python worker.py
```

## Running the application

**API server:**

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 3009
```

Or:

```bash
python main.py
```

Default: `http://0.0.0.0:3009`.


## Project structure

```
chatty_backend/
├── app/
│   ├── admin/
│   │   ├── controller/     # AppController, auth; Shopify, orchestrator, ingestion
│   │   └── routes/         # /admin/auth, /admin/app
│   └── core/
│       ├── config/         # Settings, DB init (including pgvector/tsvector)
│       ├── models/         # Tortoise models, DB controllers
│       ├── schema/         # Pydantic request/response and LLM schemas
│       ├── services/       # shopify_service, ai_orchestrator, database_executor, webcrawler
│       └── ...
├── updates/                # Changelog (see .cursor/rules.md)
├── .cursor/
│   ├── rules.md            # Development and documentation rules
│   └── agents/             # Subagents (e.g. rules-compliance-tester)
├── main.py                 # FastAPI app, lifespan, routes
├── worker.py               # Background task worker
├── pyproject.toml
└── README.md
```

## Tech stack

- **FastAPI** – API and middleware
- **Tortoise ORM** – async PostgreSQL access
- **PostgreSQL** – pgvector (embeddings), tsvector (full-text), main data store
- **Google GenAI (Gemini)** – embeddings, store DNA, intent routing, query expansion
- **Shopify API** – REST + GraphQL (orders); Python `shopifyapi` for products/collections/pages/policies
- **LangChain** – text splitting for ingestion
- **httpx** – async HTTP (e.g. Shopify token exchange, metafield API)
- **PyCryptodome** – AES encryption for chatbot API keys

## Development and documentation

- **Change log**: All notable changes are documented under `updates/` (e.g. `updates/2026-02-20.md`). See `.cursor/rules.md` for the update policy.
- **Cursor rules**: `.cursor/rules.md` defines project structure, architecture, and conventions (including structured LLM outputs and update logging).
- **Subagents**: `.cursor/agents/` can define project-specific agents (e.g. rules-compliance-tester).

## Notes

- The Shopify app must have the correct OAuth redirect URI (e.g. `{SHOPIFY_CALLBACK_DOMAIN}/api/auth/oauth/callback`).
- After OAuth, the worker runs `get_products` (and related ingestion) and `query_expander_context` (store DNA). Ensure the worker is running for full sync and DNA.
- Orchestrator `POST /admin/app/orchestrate` expects `message`, optional `chat_history`, `pre_fetched_orders`, and `chatbot_id`; it resolves store and subscription from the authenticated user when `chatbot_id` is omitted.
