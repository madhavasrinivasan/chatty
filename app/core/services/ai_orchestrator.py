"""
AI E-Commerce Orchestrator: IntentRouter (5 routes) and QueryExpander.
ORDER_SUPPORT, GENERAL_CHAT, HYBRID_SEARCH (PostgreSQL), GRAPH_SEARCH (LightRAG), PARALLEL_SEARCH.
"""
import asyncio
import json
from typing import Any

from google import genai

from app.core.config.config import settings
from app.core.schema.schema import IntentRoute, SearchPayload, SearchPayloadFilters
from app.core.services.shopify_service import get_order_status

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key,vertexai=True,
    project=settings.gemini_project,location=settings.gemini_location)
    return _client


MODEL = "gemini-2.5-flash"


VALID_ROUTES = frozenset({"ORDER_SUPPORT", "GENERAL_CHAT", "FOLLOW_UP_QUESTION", "RETURN_REQUEST", "HYBRID_SEARCH", "GRAPH_SEARCH", "PARALLEL_SEARCH"})


def _format_chat_snippet(messages: list, last_n: int) -> str:
    """Format last N messages for prompt injection."""
    if not messages:
        return ""
    tail = messages[-last_n:] if len(messages) > last_n else messages
    return " | ".join(str(m) for m in tail)


class IntentRouter:
    """
    Master switchboard: analyzes user intent and returns the first step (route)
    plus extracted_order_number when relevant.
    """

    @staticmethod
    def route(
        chat_history: list,
        current_message: str,
        subscription_plan: str,
        order_history: str = "",
    ) -> dict[str, Any]:
        """
        Returns a dict with "route" (one of ORDER_SUPPORT, GENERAL_CHAT, HYBRID_SEARCH, GRAPH_SEARCH, PARALLEL_SEARCH)
        and "extracted_order_number" (str or None). On failure, returns {"route": "HYBRID_SEARCH", "extracted_order_number": None}.
        The subscription_plan constrains which routes are allowed.
        Injects only the last 2 messages from chat_history and a brief order_history summary so the router can resolve "Where is my order?" / "Reorder my last item".
        """
        prompt = _build_router_prompt(chat_history, current_message, subscription_plan, order_history)
        try:
            client = _get_client()
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": IntentRoute.model_json_schema(),
                },
            )
            raw = getattr(response, "text", None) or str(response)
            data = json.loads(raw)
            route = data.get("route") if isinstance(data.get("route"), str) else None
            if route in VALID_ROUTES:
                order_num = data.get("extracted_order_number")
                if order_num is not None and not isinstance(order_num, str):
                    order_num = str(order_num) if order_num else None
                out = {"route": route, "extracted_order_number": order_num}
                if route == "FOLLOW_UP_QUESTION":
                    out["follow_up_message"] = (data.get("follow_up_message") or "").strip() or None
                return out
        except Exception:
            pass
        return {"route": "HYBRID_SEARCH", "extracted_order_number": None}


class QueryExpander:
    """
    Translates natural language into a structured JSON search payload for PostgreSQL,
    using store_dna, user_facts, order_history, and recent chat for context.
    """

    @staticmethod
    def expand(
        current_message: str,
        store_dna: str,
        user_facts: str = "",
        order_history: str = "",
        chat_history_snippet: list | None = None,
    ) -> dict[str, Any]:
        """
        Returns a dict matching SearchPayload shape. On failure, returns a safe default payload.
        Injects user_facts and order_history into system prompt; uses last 2-3 messages for pronoun resolution.
        Applies USER FACTS to add automatic filters or negative exclusions (e.g. -cars) for stated dislikes.
        """
        prompt = _build_expander_prompt(
            current_message,
            store_dna,
            user_facts=user_facts or "",
            order_history=order_history or "",
            chat_history_snippet=chat_history_snippet or [],
        )
        try:
            client = _get_client()
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": SearchPayload.model_json_schema(),
                },
            )
            raw = getattr(response, "text", None) or str(response)
            data = json.loads(raw)
            # Ensure filters dict has color/size if missing
            if "filters" not in data or not isinstance(data["filters"], dict):
                data["filters"] = {"color": None, "size": None}
            else:
                data["filters"].setdefault("color")
                data["filters"].setdefault("size")
            # Ensure rrf_weights present and sum to 1.0
            rrf = data.get("rrf_weights")
            if not isinstance(rrf, dict):
                data["rrf_weights"] = {"keyword_weight": 0.5, "vector_weight": 0.5}
            else:
                kw = float(rrf.get("keyword_weight", 0.5))
                vw = float(rrf.get("vector_weight", 0.5))
                total = kw + vw
                if total <= 0:
                    kw, vw = 0.5, 0.5
                else:
                    kw, vw = kw / total, vw / total
                data["rrf_weights"] = {"keyword_weight": kw, "vector_weight": vw}
            return data
        except Exception:
            return _default_search_payload(current_message)


def _build_expander_prompt(
    current_message: str,
    store_dna: str,
    user_facts: str = "",
    order_history: str = "",
    chat_history_snippet: list | None = None,
) -> str:
    memory_block = ""
    if (user_facts or "").strip() or (order_history or "").strip():
        memory_block = "USER FACTS: " + (user_facts or "").strip() + " | PAST ORDERS: " + (order_history or "").strip() + "\n\n"
    history_block = ""
    if chat_history_snippet:
        last_2_3 = chat_history_snippet[-3:] if len(chat_history_snippet) > 3 else chat_history_snippet
        history_block = "Recent chat (last 2-3 messages): " + _format_chat_snippet(last_2_3, 3) + "\n\n"
    instruction = (
        "Use the chat history and past orders to understand what the user is referring to. "
        "Apply the USER FACTS to add automatic filters or negative exclusions (e.g. -cars) if the user has a stated dislike.\n\n"
    )
    return f"""You are an expert E-Commerce Query Expander for a store with the following DNA: {store_dna}

{memory_block}{history_block}{instruction}Analyze the user's message to extract the core search intent, implied filters, sorting preferences, and the optimal Reciprocal Rank Fusion (RRF) weights.

CRITICAL RULES FOR PRODUCT QUERIES:
1. PRESERVE MODIFIERS: DO NOT drop important adjectives, colors, shapes, or specific identifiers from the keywords. 
2. MULTIPLE ITEMS: If the user asks for multiple distinct items, you MUST separate the distinct items with the exact string " OR " in `search_keywords` (e.g., "cloth diaper pack OR green kitty grooming kit"). You MUST classify this as EXACT/SPECIFIC (keyword_weight: 0.8).
3. CONCENTRATED SEMANTICS: `semantic_context` MUST ALWAYS contain the positive, underlying concepts of the product. It must be a comma-separated list of abstract concepts, vibes, or generic product types (e.g., "baby blanket, receiving blanket, newborn comfort"). NEVER LEAVE THIS BLANK, even if using exclusions.
4. EXTRACTING FILTERS: Only put colors/sizes into `filters` if they are generic variants. 
5. EXCLUSIONS (NEGATIVE SEARCH): If the user says they do NOT want something (e.g. "no cars"), OR if USER FACTS indicate a dislike, append the excluded words to `search_keywords` with a minus sign (e.g. "muslin receiving blanket -car -vehicle"). CRITICAL: The `semantic_context` MUST still contain the positive concepts (e.g., "baby blanket, nursery") so the vector search knows what to look for!

CRITICAL RULES FOR NON-PRODUCT QUERIES (Policies, FAQs, About Us):
- If the user asks about the store in general: Set search_keywords to "about us, our story". Set semantic_context to "brand mission, history".
- If the user asks about a specific policy: Extract ONLY the exact policy for search_keywords. Set semantic_context to "shipping details, delivery" OR "refund conditions". Set rrf_weights.keyword_weight to 0.4 and vector_weight to 0.6.
- For other non-product queries, set keyword_weight to 0.2 and vector_weight to 0.8.

SORTING RULES:
- If the user says "cheapest", "lowest price", "budget" -> sort_column "price", sort_order "ASC".
- If the user says "most expensive", "premium" -> sort_column "price", sort_order "DESC".
- If the user says "best", "top rated" -> sort_column "rating", sort_order "DESC".
- If the user says "newest", "latest" -> sort_column "created_at", sort_order "DESC".
- Otherwise -> sort_column and sort_order should be null.

RRF WEIGHT RULES (must sum to 1.0):
- EXACT/SPECIFIC (keyword_weight: 0.8, vector_weight: 0.2): Use this if the user names specific nouns, designs, animals, exact models, MULTIPLE distinct products, or uses EXCLUSIONS.
- VIBE/ABSTRACT (keyword_weight: 0.2, vector_weight: 0.8): Use this ONLY for pure vibes, aesthetics, or conceptual queries.
- BALANCED (keyword_weight: 0.5, vector_weight: 0.5): Use this for generic broad categories.

OUTPUT INSTRUCTIONS:
Output ONLY a raw, valid JSON object. Do not wrap the JSON in markdown code blocks.
The JSON MUST match this schema:
{{
  "search_keywords": "string",
  "semantic_context": "string",
  "sort_column": "price" | "rating" | "created_at" | null,
  "sort_order": "ASC" | "DESC" | null,
  "limit": 20,
  "filters": {{"color": "string or null", "size": "string or null"}},
  "rrf_weights": {{"keyword_weight": float, "vector_weight": float}}
}}
- limit is default set to 20
User message: {current_message}"""


def _build_router_prompt(
    chat_history: list,
    current_message: str,
    subscription_plan: str,
    order_history: str = "",
) -> str:
    # Only last 2 messages so router can resolve pronouns (e.g. "Do you have that in blue?")
    history_snippet = ""
    if chat_history:
        tail = chat_history[-2:] if len(chat_history) > 2 else chat_history
        history_snippet = "Recent chat (last 2 messages): " + _format_chat_snippet(tail, 2) + "\n\n"
    order_snippet = ""
    if (order_history or "").strip():
        order_snippet = "User's past orders (for intent 'Where is my order?' / 'Reorder my last item'): " + (order_history or "").strip()[:1500] + "\n\n"
    return f"""You are an Intent Router for an AI Agent. The current store is on the {subscription_plan} plan.

Analyze the user's intent and choose the absolute first step, following these rules.

ROUTES (exactly one):
1. ORDER_SUPPORT: Use ONLY when the user has already provided an order number (e.g. #1001, 1001). Extract it in "extracted_order_number". If the user clearly wants order status/tracking but did NOT give an order number, do NOT use ORDER_SUPPORT; use FOLLOW_UP_QUESTION instead and ask for the order number in "follow_up_message".
2. GENERAL_CHAT: For simple greetings or small-talk (e.g. "hi", "hello", "thanks", "what can you do?").
3. FOLLOW_UP_QUESTION: Use when the user's intent is ambiguous or critical information is missing. This includes: (a) order status/tracking intent but no order number given (e.g. "Where is my order?" → ask "Could you share your order number so I can look up the status?"), (b) product/search intent but missing size, color, budget, which variant, baby age, etc. Use chat history to infer intent, then output a short, friendly "follow_up_message". Do NOT use for clear product or policy queries that have enough context.
4. RETURN_REQUEST: Use when the user mentions returning an item, getting a refund, or exchanging an order (e.g. "I want to return this", "Can I get a refund?", "I need to exchange my order").
5. HYBRID_SEARCH: For product searches, finding cheapest items, OR asking about store policies, shipping, FAQs, and "About Us" information. Targets PostgreSQL.
6. GRAPH_SEARCH: Deep technical/manual questions (e.g. "How to descale the machine", "assembly instructions").
7. PARALLEL_SEARCH: Multi-part questions requiring BOTH product search AND manual traversal (e.g. "Do you sell the Breville Bambino, and how do I descale it?").

PLAN CONSTRAINTS:
- If the plan is "starter" or "basic": NEVER choose GRAPH_SEARCH or PARALLEL_SEARCH. For those queries, choose HYBRID_SEARCH instead.
- If the plan is "enterprise": You MAY choose any of the 5 routes.

Use the past orders summary below to classify intent accurately when the user says "Where is my order?" or "I want to reorder my last item."

Output a JSON object with:
- "route": exactly one of ORDER_SUPPORT, GENERAL_CHAT, FOLLOW_UP_QUESTION, RETURN_REQUEST, HYBRID_SEARCH, GRAPH_SEARCH, PARALLEL_SEARCH
- "extracted_order_number": the order number if present (e.g. "#1001"), otherwise null
- "follow_up_message": when route is FOLLOW_UP_QUESTION, a short friendly question to ask the user (e.g. "What size are you looking for?"); otherwise null

{order_snippet}{history_snippet}Current user message: {current_message}"""


RETURN_SPECIALIST_SYSTEM = """You are a Return Specialist. Look at the user's PAST ORDERS.
First, ask the user for their exact order number. Once they provide it, you MUST output this exact string at the end of your response: `[ACTION:FETCH_ORDER | order: #<order_number>]`
Do NOT ask for the item or reason until after they have been shown the list of items from that order.
Once the user has selected an item and provided a reason, output this exact string at the end of your message: `[ACTION:CREATE_RETURN | order: #<order_number> | item: <item_title> | reason: <reason>]`
You CANNOT process refunds directly."""


def _get_return_specialist_response(
    current_message: str,
    chat_history: list,
    order_history: str,
) -> str:
    """Generate Return Specialist reply using chat_history and order_history only (no vector search)."""
    try:
        client = _get_client()
        order_block = f"PAST ORDERS:\n{order_history.strip()}\n\n" if (order_history or "").strip() else ""
        history_lines = []
        for m in (chat_history or [])[-10:]:
            role = (m.get("role") if isinstance(m, dict) else None) or "user"
            content = (m.get("content") if isinstance(m, dict) else None) or str(m)
            if content:
                history_lines.append(f"{role}: {content}")
        history_block = "CHAT HISTORY:\n" + "\n".join(history_lines) + "\n\n" if history_lines else ""
        prompt = f"""{RETURN_SPECIALIST_SYSTEM}

{order_block}{history_block}Current user message: {current_message}

Reply as the Return Specialist. If the user has given their order number (and no item list has been shown yet), end your message with [ACTION:FETCH_ORDER | order: #<order_number>]. If they have already provided order number, item, and reason, end with [ACTION:CREATE_RETURN | ...] as specified."""
        response = client.models.generate_content(model=MODEL, contents=prompt)
        raw = getattr(response, "text", None) or str(response)
        return (raw or "").strip() or "I can help with returns. Please share your order number, the item you want to return, and the reason."
    except Exception as e:
        print(f"_get_return_specialist_response error: {e}")
        return "I can help with returns. Please share your order number, the item you want to return, and the reason."


def _get_general_chat_response(current_message: str, chat_history: list) -> str:
    """Generate a short conversational reply for GENERAL_CHAT (greetings, off-topic)."""
    try:
        client = _get_client()
        prompt = f"""You are a friendly e-commerce store assistant. The user said: "{current_message}"
Reply in one or two short sentences. Be helpful and conversational. Do not search for products or orders."""
        response = client.models.generate_content(model=MODEL, contents=prompt)
        raw = getattr(response, "text", None) or str(response)
        return (raw or "").strip() or "How can I help you today?"
    except Exception:
        return "How can I help you today?"


def _default_search_payload(current_message: str) -> dict[str, Any]:
    """Safe fallback when QueryExpander fails."""
    keywords = (current_message or "").strip()[:200] or ""
    return {
        "search_keywords": keywords,
        "semantic_context": "",
        "sort_column": None,
        "sort_order": None,
        "limit": 5,
        "filters": {"color": None, "size": None},
        "rrf_weights": {"keyword_weight": 0.5, "vector_weight": 0.5},
    }


async def process_user_query(
    message: str,
    chat_history: list,
    pre_fetched_orders: list | dict,
    store_dna: str,
    subscription_plan: str,
    store_name: str | None = None,
    access_token: str | None = None,
    user_facts: str = "",
    order_history: str = "",
    previous_session_history: str = "",
) -> dict[str, Any]:
    """
    Step 1: Call IntentRouter (last 2 of chat_history, message, order_history) -> route + extracted_order_number.
    Step 2:
      - ORDER_SUPPORT: if no order_id return prompting; if order_id and store credentials, call Shopify order status and return order_status.
      - GENERAL_CHAT: return a direct conversational response (Gemini).
      - HYBRID_SEARCH: call QueryExpander (user_facts, order_history, last 2-3 of chat_history), return search_payload.
      - GRAPH_SEARCH / PARALLEL_SEARCH: placeholder.
    Caller may pass user_facts, order_history, previous_session_history for use in router/expander/synthesizer.
    On router failure, falls back to HYBRID_SEARCH with default search payload.
    """
    # Step 1: Route (sync call in thread); inject order_history and last 2 messages
    try:
        route_result = await asyncio.to_thread(
            IntentRouter.route,
            chat_history or [],
            message or "",
            subscription_plan or "starter",
            order_history or "",
        )
    except Exception:
        route_result = {"route": "HYBRID_SEARCH", "extracted_order_number": None}

    route = route_result.get("route") if isinstance(route_result, dict) else None
    extracted_order_number = route_result.get("extracted_order_number") if isinstance(route_result, dict) else None
    # Enforce plan constraints on routes
    plan_normalized = (subscription_plan or "starter").lower()
    if route not in VALID_ROUTES:
        route = "HYBRID_SEARCH"
    elif plan_normalized in ("starter", "basic") and route in ("GRAPH_SEARCH", "PARALLEL_SEARCH"):
        # Downgrade to HYBRID_SEARCH for non-enterprise plans
        route = "HYBRID_SEARCH"

    # Step 2: Branch by route
    if route == "ORDER_SUPPORT":
        order_id = (extracted_order_number or "").strip()
        if not order_id:
            return {"route": "ORDER_SUPPORT", "message": "need orderId", "prompting": True}
        # Order ID present: call Shopify API for order status when store credentials are provided
        if store_name and access_token:
            try:
                order_status = await asyncio.to_thread(
                    get_order_status,
                    store_name,
                    access_token,
                    order_id,
                )
                return {"route": "ORDER_SUPPORT", "extracted_order_number": order_id, "order_status": order_status}
            except Exception as e:
                return {
                    "route": "ORDER_SUPPORT",
                    "extracted_order_number": order_id,
                    "order_status": {"found": False, "message": str(e)},
                }
        return {"route": "ORDER_SUPPORT", "extracted_order_number": order_id}

    if route == "GENERAL_CHAT":
        reply = await asyncio.to_thread(
            _get_general_chat_response,
            message or "",
            chat_history or [],
        )
        return {"route": "GENERAL_CHAT", "conversational_response": reply}

    if route == "FOLLOW_UP_QUESTION":
        follow_up = (route_result.get("follow_up_message") or "").strip() if isinstance(route_result, dict) else ""
        if not follow_up:
            follow_up = "Could you tell me a bit more so I can help you better?"
        return {"route": "FOLLOW_UP_QUESTION", "follow_up_message": follow_up}

    if route == "RETURN_REQUEST":
        reply = await asyncio.to_thread(
            _get_return_specialist_response,
            message or "",
            chat_history or [],
            order_history or "",
        )
        return {"route": "RETURN_REQUEST", "return_specialist_response": reply}

    if route == "HYBRID_SEARCH":
        chat_snippet = (chat_history or [])[-3:] if (chat_history or []) else []
        try:
            payload = await asyncio.to_thread(
                QueryExpander.expand,
                message or "",
                store_dna or "",
                user_facts or "",
                order_history or "",
                chat_snippet,
            )
        except Exception:
            payload = _default_search_payload(message or "")
        return {"route": "HYBRID_SEARCH", "search_payload": payload}

    # GRAPH_SEARCH or PARALLEL_SEARCH
    print("GRAPH/PARALLEL execution coming soon", flush=True)
    return {"route": route}
