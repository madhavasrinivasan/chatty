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

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


MODEL = "gemini-2.5-flash"


VALID_ROUTES = frozenset({"ORDER_SUPPORT", "GENERAL_CHAT", "HYBRID_SEARCH", "GRAPH_SEARCH", "PARALLEL_SEARCH"})


class IntentRouter:
    """
    Master switchboard: analyzes user intent and returns the first step (route)
    plus extracted_order_number when relevant.
    """

    @staticmethod
    def route(chat_history: list, current_message: str) -> dict[str, Any]:
        """
        Returns a dict with "route" (one of ORDER_SUPPORT, GENERAL_CHAT, HYBRID_SEARCH, GRAPH_SEARCH, PARALLEL_SEARCH)
        and "extracted_order_number" (str or None). On failure, returns {"route": "HYBRID_SEARCH", "extracted_order_number": None}.
        """
        prompt = _build_router_prompt(chat_history, current_message)
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
                return {"route": route, "extracted_order_number": order_num}
        except Exception:
            pass
        return {"route": "HYBRID_SEARCH", "extracted_order_number": None}


class QueryExpander:
    """
    Translates natural language into a structured JSON search payload for PostgreSQL,
    using store_dna for context.
    """

    @staticmethod
    def expand(current_message: str, store_dna: str) -> dict[str, Any]:
        """
        Returns a dict matching SearchPayload shape. On failure, returns a safe default payload.
        """
        prompt = _build_expander_prompt(current_message, store_dna)
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


def _build_router_prompt(chat_history: list, current_message: str) -> str:
    history_snippet = ""
    if chat_history:
        tail = chat_history[-5:] if len(chat_history) > 5 else chat_history
        history_snippet = "Recent chat (last messages): " + " | ".join(
            str(m) for m in tail
        ) + "\n\n"
    return f"""You are the master switchboard for an e-commerce assistant. Analyze the user's intent and choose the absolute first step.

ROUTES (exactly one):
1. ORDER_SUPPORT: User is asking about an existing order, tracking, or shipping status. Extract the order number if mentioned (e.g. #1001).
2. GENERAL_CHAT: Greetings, off-topic chat, or basic bot interactions (e.g. "hi", "thanks", "what can you do?").
3. HYBRID_SEARCH: Standard product searches, pricing, or basic store policy queries. Targets PostgreSQL.
4. GRAPH_SEARCH: Deep queries requiring PDF/manual traversal (e.g. "How to descale the machine", "assembly instructions").
5. PARALLEL_SEARCH: Multi-part question requiring BOTH product search AND manual traversal (e.g. "Do you sell the Breville Bambino, and how do I descale it?").

Output a JSON object with:
- "route": exactly one of ORDER_SUPPORT, GENERAL_CHAT, HYBRID_SEARCH, GRAPH_SEARCH, PARALLEL_SEARCH
- "extracted_order_number": the order number if present (e.g. "#1001"), otherwise null

{history_snippet}Current user message: {current_message}"""


def _build_expander_prompt(current_message: str, store_dna: str) -> str:
    return f"""You are an expert E-Commerce Query Expander for a store with the following DNA: {store_dna}

Your job is to take a user's natural language search query and translate it into a structured JSON search payload for a Hybrid PostgreSQL database, including dynamic RRF (Reciprocal Rank Fusion) weights.

Analyze the user's message to extract the core search intent, implied filters, sorting preferences, and determine the optimal search weights.

OUTPUT INSTRUCTIONS:
Output a raw, valid JSON object only. Do not wrap the JSON in markdown code blocks. Do not include conversational text.

Use this exact JSON schema:
- search_keywords: Core product for text search (e.g. "watch", "running shoes"). Exclude adjectives like "cheapest".
- semantic_context: Full context or vibe for vector search (e.g. "a watch that looks good with a blue suit").
- sort_column: "price" | "rating" | "created_at" | null
- sort_order: "ASC" | "DESC" | null
- limit: Integer (default 5).
- filters: {{ "color": extracted color or null, "size": extracted size or null }}
- rrf_weights: {{ "keyword_weight": float 0.0-1.0, "vector_weight": float 0.0-1.0 }} (must sum to 1.0)

RULES FOR SORTING:
- "cheapest", "lowest price", "budget" -> sort_column "price", sort_order "ASC"
- "most expensive", "premium", "highest price" -> sort_column "price", sort_order "DESC"
- "best", "top rated" -> sort_column "rating", sort_order "DESC"
- "newest", "latest" -> sort_column "created_at", sort_order "DESC"
- Otherwise -> both null.

RULES FOR RRF WEIGHTS (must sum to 1.0):
- EXACT/SPECIFIC: If the query contains specific product names, brand names, SKUs, or exact models (e.g. "Air Force 1 size 10", "Breville Bambino Plus"), set keyword_weight to 0.8 and vector_weight to 0.2.
- VIBE/ABSTRACT: If the query is descriptive, aesthetic, or conceptual (e.g. "something for a summer party", "minimalist desk setup", "gift for my mom"), set vector_weight to 0.8 and keyword_weight to 0.2.
- BALANCED: If the query is a generic product category (e.g. "red running shoes", "leather wallet"), set both to 0.5.

User message: {current_message}"""


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
) -> dict[str, Any]:
    """
    Step 1: Call IntentRouter (chat_history, message) -> route + extracted_order_number.
    Step 2:
      - ORDER_SUPPORT: return message "Triggering Shopify API for Order: [extracted_order_number]"
      - GENERAL_CHAT: return a direct conversational response (Gemini).
      - HYBRID_SEARCH: call QueryExpander, return search_payload.
      - GRAPH_SEARCH / PARALLEL_SEARCH: placeholder.
    On router failure, falls back to HYBRID_SEARCH with default search payload.
    """
    # Step 1: Route (sync call in thread)
    try:
        route_result = await asyncio.to_thread(
            IntentRouter.route,
            chat_history or [],
            message or "",
        )
    except Exception:
        route_result = {"route": "HYBRID_SEARCH", "extracted_order_number": None}

    route = route_result.get("route") if isinstance(route_result, dict) else None
    extracted_order_number = route_result.get("extracted_order_number") if isinstance(route_result, dict) else None
    if route not in VALID_ROUTES:
        route = "HYBRID_SEARCH"

    # Step 2: Branch by route
    if route == "ORDER_SUPPORT":
        order_str = extracted_order_number or ""
        msg = f"Triggering Shopify API for Order: {order_str}"
        print(msg, flush=True)
        return {"route": "ORDER_SUPPORT", "extracted_order_number": extracted_order_number, "message": msg}

    if route == "GENERAL_CHAT":
        reply = await asyncio.to_thread(
            _get_general_chat_response,
            message or "",
            chat_history or [],
        )
        return {"route": "GENERAL_CHAT", "conversational_response": reply}

    if route == "HYBRID_SEARCH":
        try:
            payload = await asyncio.to_thread(
                QueryExpander.expand,
                message or "",
                store_dna or "",
            )
        except Exception:
            payload = _default_search_payload(message or "")
        return {"route": "HYBRID_SEARCH", "search_payload": payload}

    # GRAPH_SEARCH or PARALLEL_SEARCH
    print("GRAPH/PARALLEL execution coming soon", flush=True)
    return {"route": route}
