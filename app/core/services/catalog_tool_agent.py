"""
Bounded catalog tool agent — for complex personalization / multi-hop product questions.

Prefetch (orders, cart, facts) stays in the prompt.
Tools only refine catalog retrieval (+ optional live order status):

  - search_catalog  → execute_search (Comez + Shopify share DB catalog)
  - get_product     → adapter product APIs, with DB fallback
  - get_order_status → adapter.get_order_status (on-demand)

Max 2 tool rounds, then hand accumulated hits to normal response synthesis.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.config.config import settings
from app.core.config.gemini_client import generate_content_text
from app.core.services.database_executor import execute_search
from app.core.services.response_synthesis import get_variant_data_from_db
from app.core.services.token_tracker import estimate_tokens

MODEL = settings.gemini_router_model or "gemini-2.5-flash"

MAX_ROUNDS = 2
MAX_TOOLS_PER_ROUND = 3
SEARCH_LIMIT = 12

TOOL_SPECS = [
    {
        "name": "search_catalog",
        "description": (
            "Search the store product catalog (keyword + optional semantic). "
            "Handles cheapest/most expensive via sort_column/sort_order, and category via filters.category. "
            "Use specific product terms from cart/past orders when recommending; "
            "for price questions prefer empty semantic_context so SQL can sort by price."
        ),
        "parameters": {
            "search_keywords": "string — core terms / category words (use OR for alternatives). Empty string OK for whole-catalog cheapest.",
            "semantic_context": "string — vibe only; leave EMPTY when sorting by price (cheapest/lowest/expensive)",
            "sort_column": '"price" | "created_at" | "rating" | null',
            "sort_order": '"ASC" | "DESC" | null — ASC=cheapest, DESC=most expensive',
            "limit": "int 1-20, default 12 (use 5 for cheapest/expensive)",
            "color": "optional string",
            "size": "optional string",
            "category": "optional string — category/collection theme matched in title/content (e.g. towels, apparel)",
        },
        "examples": [
            {
                "user": "find the cheapest product",
                "args": {
                    "search_keywords": "",
                    "semantic_context": "",
                    "sort_column": "price",
                    "sort_order": "ASC",
                    "limit": 5,
                },
            },
            {
                "user": "cheapest product in towels / muslin category",
                "args": {
                    "search_keywords": "towel OR muslin",
                    "semantic_context": "",
                    "sort_column": "price",
                    "sort_order": "ASC",
                    "category": "towel",
                    "limit": 5,
                },
            },
            {
                "user": "most expensive blanket",
                "args": {
                    "search_keywords": "blanket",
                    "semantic_context": "",
                    "sort_column": "price",
                    "sort_order": "DESC",
                    "limit": 5,
                },
            },
            {
                "user": "recommend something like my muslin cart item",
                "args": {
                    "search_keywords": "muslin towel OR organic bath towel",
                    "semantic_context": "complement to muslin receiving blanket",
                    "sort_column": None,
                    "sort_order": None,
                    "limit": 12,
                },
            },
        ],
    },
    {
        "name": "get_product",
        "description": (
            "Load one product by catalog id, Comez/Shopify product id, or handle/slug. "
            "Use after search when you need variants/options for a specific hit."
        ),
        "parameters": {
            "product_id": "optional string — store_knowledge id or comez_*/Shopify id",
            "handle": "optional string — product handle/slug",
        },
    },
    {
        "name": "get_order_status",
        "description": (
            "Live lookup for ONE order number when the user wants status/tracking "
            "(e.g. A8081). Do not use just to list past orders — those are already in context."
        ),
        "parameters": {"order_number": "string — display order id without requiring #"},
    },
]


def _summarize_cart(cart_items: list[dict[str, Any]] | None) -> str:
    if not cart_items:
        return "(empty cart)"
    lines = []
    for i in cart_items[:8]:
        if not isinstance(i, dict):
            continue
        lines.append(
            f"- {i.get('title') or 'item'} × {i.get('quantity') or 1} @ {i.get('price')}"
        )
    return "\n".join(lines) if lines else "(empty cart)"


def _compact_search_rows(rows: list[dict[str, Any]], *, cap: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows[:cap]:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "id": r.get("id"),
                "shopify_product_id": r.get("shopify_product_id"),
                "title": r.get("title"),
                "price": r.get("price"),
                "handle": r.get("handle"),
                "image_url": r.get("image_url"),
                "url": r.get("url"),
                "content": ((r.get("content") or "")[:280]),
            }
        )
    return out


def _merge_hits(bucket: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    seen = {str(r.get("id") or r.get("shopify_product_id")) for r in bucket}
    for r in rows:
        key = str(r.get("id") or r.get("shopify_product_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        bucket.append(r)


def _planner_prompt(
    *,
    message: str,
    chat_history: list,
    store_dna: str,
    user_facts: str,
    order_history: str,
    cart_items: list[dict[str, Any]] | None,
    prior_tool_results: list[dict[str, Any]],
    round_idx: int,
) -> str:
    hist = ""
    if chat_history:
        tail = chat_history[-4:]
        bits = []
        for m in tail:
            if not isinstance(m, dict):
                continue
            bits.append(f"{m.get('role')}: {(m.get('content') or '')[:200]}")
        hist = "\n".join(bits)

    prior = json.dumps(prior_tool_results[-6:], default=str)[:6000] if prior_tool_results else "[]"

    return f"""You are a catalog research agent for an e-commerce chatbot.
Orders, cart, and facts are ALREADY loaded — do NOT ask for tools that re-fetch them.
Your job is better PRODUCT retrieval for a personalized recommendation.

Store DNA: {store_dna or "online store"}
USER FACTS: {(user_facts or "")[:800]}
PAST ORDERS: {(order_history or "")[:1500]}
CURRENT CART:
{_summarize_cart(cart_items)}

Recent chat:
{hist or "(none)"}

User message: {message}

Round {round_idx + 1} of {MAX_ROUNDS}.
Prior tool results (JSON): {prior}

Available tools:
{json.dumps(TOOL_SPECS, indent=2)}

Decide:
- If you still need better catalog matches or one product detail / one order status, output tool_calls.
- If you have enough catalog hits to recommend (or tools already ran), output done=true and no tool_calls.

Rules:
- Prefer specific keywords drawn from cart titles / past order themes (fabric, category, complementary items).
- Avoid vague search_keywords like "baby products" or "best products".
- Cheapest / lowest / budget → sort_column=price, sort_order=ASC, semantic_context="".
- Most expensive / premium → sort_column=price, sort_order=DESC, semantic_context="".
- Category scoped (e.g. cheapest in towels) → set search_keywords AND/OR category filter, plus price sort.
- Max {MAX_TOOLS_PER_ROUND} tool_calls this round; prefer search_catalog first.
- get_order_status only when a specific order number should be looked up live.

Output ONLY valid JSON:
{{
  "done": false,
  "reason": "short why",
  "tool_calls": [
    {{"name": "search_catalog", "args": {{"search_keywords": "...", "semantic_context": "...", "limit": 12}}}}
  ]
}}
OR
{{
  "done": true,
  "reason": "enough results to answer",
  "tool_calls": []
}}
"""


def _parse_planner(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {"done": True, "tool_calls": [], "reason": "parse_error"}
    if not isinstance(data, dict):
        return {"done": True, "tool_calls": [], "reason": "bad_shape"}
    calls = data.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []
    cleaned = []
    for c in calls[:MAX_TOOLS_PER_ROUND]:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        if name in ("search_catalog", "get_product", "get_order_status"):
            cleaned.append({"name": name, "args": args})
    done = bool(data.get("done")) or not cleaned
    return {"done": done, "tool_calls": cleaned, "reason": data.get("reason")}


async def _tool_search_catalog(store_id: int, args: dict[str, Any]) -> dict[str, Any]:
    keywords = (args.get("search_keywords") or "").strip()
    semantic = (args.get("semantic_context") or "").strip()
    sort_column = args.get("sort_column")
    sort_order = args.get("sort_order")
    # Price sorts must not use embeddings — leave semantic empty
    if sort_column == "price":
        semantic = ""
    elif not semantic and not (sort_column or keywords):
        semantic = "catalog browse"
    try:
        limit = int(args.get("limit") or SEARCH_LIMIT)
    except (TypeError, ValueError):
        limit = SEARCH_LIMIT
    limit = max(1, min(limit, 20))
    if sort_column == "price":
        limit = min(limit, 8)
    payload = {
        "search_keywords": keywords,
        "semantic_context": semantic,
        "sort_column": sort_column,
        "sort_order": sort_order,
        "limit": limit,
        "filters": {
            "color": args.get("color"),
            "size": args.get("size"),
            "category": args.get("category"),
        },
        "rrf_weights": {
            "keyword_weight": float(args.get("keyword_weight", 0.35)),
            "vector_weight": float(args.get("vector_weight", 0.65)),
        },
    }
    rows = await execute_search(store_id=store_id, payload=payload)
    compact = _compact_search_rows(rows)
    return {
        "ok": True,
        "tool": "search_catalog",
        "count": len(compact),
        "products": compact,
        "sort": {"column": sort_column, "order": sort_order},
        "_rows": rows,  # full rows for synthesis (stripped before next LLM prompt)
    }


async def _tool_get_product(
    *,
    store_id: int | None,
    adapter: Any,
    args: dict[str, Any],
) -> dict[str, Any]:
    product_id = args.get("product_id")
    handle = (args.get("handle") or "").strip() or None
    detail: dict[str, Any] = {}

    if adapter is not None:
        try:
            if handle:
                detail = await adapter.get_product_by_handle(handle)
            elif product_id not in (None, ""):
                # Prefer platform id when non-numeric; numeric often DB pk
                pid_str = str(product_id)
                if not pid_str.isdigit():
                    detail = await adapter.get_product_by_id(product_id)
        except Exception as e:
            detail = {"found": False, "message": str(e)}

    db_row: dict[str, Any] = {}
    if store_id is not None and (product_id not in (None, "") or handle):
        try:
            key = str(product_id) if product_id not in (None, "") else str(handle)
            db_row = await get_variant_data_from_db(key, store_id)
        except Exception:
            db_row = {}

    found = bool(detail.get("found")) or bool(db_row)
    # Normalize a synthesis-friendly row when DB hit exists
    synth_row = None
    if db_row:
        synth_row = {
            "id": product_id if str(product_id or "").isdigit() else None,
            "shopify_product_id": None if str(product_id or "").isdigit() else product_id,
            "title": db_row.get("title"),
            "price": db_row.get("price"),
            "handle": db_row.get("handle") or handle,
            "image_url": db_row.get("image_url"),
            "content": db_row.get("title") or "",
            "url": None,
            "variants_preview": (db_row.get("variants") or [])[:5],
        }

    return {
        "ok": found,
        "tool": "get_product",
        "product_id": product_id,
        "handle": handle,
        "adapter": {
            "found": detail.get("found"),
            "product_id": detail.get("product_id"),
            "handle": detail.get("handle"),
            "variants": (detail.get("variants") or [])[:8],
        }
        if detail
        else None,
        "db": {
            "title": db_row.get("title"),
            "price": db_row.get("price"),
            "handle": db_row.get("handle"),
            "variant_count": len(db_row.get("variants") or []),
        }
        if db_row
        else None,
        "_row": synth_row,
    }


async def _tool_get_order_status(adapter: Any, args: dict[str, Any]) -> dict[str, Any]:
    order_number = (args.get("order_number") or "").strip()
    if not order_number:
        return {"ok": False, "tool": "get_order_status", "message": "missing order_number"}
    if adapter is None:
        return {"ok": False, "tool": "get_order_status", "message": "no commerce adapter"}
    try:
        status = await adapter.get_order_status(order_number)
        # Compact for planner
        return {
            "ok": bool(status.get("found")),
            "tool": "get_order_status",
            "order_name": status.get("order_name"),
            "financial_status": status.get("financial_status"),
            "fulfillment_status": status.get("fulfillment_status"),
            "line_items": [
                li.get("display_line") or li.get("title")
                for li in (status.get("line_items") or [])[:6]
                if isinstance(li, dict)
            ],
            "message": status.get("message"),
            "_order_status": status,
        }
    except Exception as e:
        return {"ok": False, "tool": "get_order_status", "message": str(e)}


async def run_catalog_tool_agent(
    *,
    message: str,
    chat_history: list,
    store_id: int,
    store_dna: str = "",
    user_facts: str = "",
    order_history: str = "",
    cart_items: list[dict[str, Any]] | None = None,
    adapter: Any = None,
    seed_search_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run up to MAX_ROUNDS of catalog tools. Returns:
      {
        route: "CATALOG_AGENT",
        search_results: [...],   # for generate_final_response
        order_status: dict|None,
        tool_trace: [...],
        token_usage: {...},
      }
    """
    accumulated: list[dict[str, Any]] = []
    order_status: dict[str, Any] | None = None
    prior_for_llm: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    usage_parts: list[dict[str, dict[str, int]]] = []

    # Optional seed search from router payload (first cheap hit before planning)
    if seed_search_payload and isinstance(seed_search_payload, dict):
        try:
            seed = dict(seed_search_payload)
            # Nudge vague seeds toward cart/order-aware semantics if blank-ish
            kw = (seed.get("search_keywords") or "").strip().lower()
            if kw in ("", "baby products", "products", "best products"):
                cart_titles = [
                    str(i.get("title") or "")
                    for i in (cart_items or [])
                    if isinstance(i, dict) and i.get("title")
                ]
                if cart_titles:
                    seed["search_keywords"] = " OR ".join(
                        t.split("-")[0].strip() for t in cart_titles[:2]
                    )
                    seed["semantic_context"] = (
                        (seed.get("semantic_context") or "")
                        + ", complementary to cart: "
                        + ", ".join(cart_titles[:2])
                    ).strip(", ")
            seed.setdefault("limit", SEARCH_LIMIT)
            rows = await execute_search(store_id=store_id, payload=seed)
            _merge_hits(accumulated, rows)
            compact = _compact_search_rows(rows)
            prior_for_llm.append({"tool": "search_catalog", "seed": True, "count": len(compact), "products": compact})
            tool_trace.append({"name": "search_catalog", "seed": True, "count": len(compact)})
        except Exception as e:
            print(f"catalog agent seed search failed: {e}")

    for round_idx in range(MAX_ROUNDS):
        prompt = _planner_prompt(
            message=message,
            chat_history=chat_history or [],
            store_dna=store_dna,
            user_facts=user_facts,
            order_history=order_history,
            cart_items=cart_items,
            prior_tool_results=prior_for_llm,
            round_idx=round_idx,
        )
        try:
            raw = await asyncio.to_thread(
                generate_content_text,
                MODEL,
                prompt,
                {
                    "response_mime_type": "application/json",
                    "thinking_config": {"thinking_budget": 0},
                    "max_output_tokens": 512,
                },
            )
        except Exception as e:
            print(f"catalog agent planner failed: {e}")
            break

        usage_parts.append(
            {
                f"catalog_agent_round_{round_idx}": {
                    "input_tokens": estimate_tokens(prompt),
                    "output_tokens": estimate_tokens(raw or ""),
                }
            }
        )
        plan = _parse_planner(raw or "")
        print(f"🛒 catalog_agent round={round_idx} plan={plan}")
        if plan.get("done") or not plan.get("tool_calls"):
            break

        async def _run_one(call: dict[str, Any]) -> dict[str, Any]:
            name = call["name"]
            args = call.get("args") or {}
            if name == "search_catalog":
                return await _tool_search_catalog(store_id, args)
            if name == "get_product":
                return await _tool_get_product(store_id=store_id, adapter=adapter, args=args)
            if name == "get_order_status":
                return await _tool_get_order_status(adapter, args)
            return {"ok": False, "tool": name, "message": "unknown tool"}

        results = await asyncio.gather(
            *[_run_one(c) for c in plan["tool_calls"]],
            return_exceptions=True,
        )

        for res in results:
            if isinstance(res, Exception):
                prior_for_llm.append({"ok": False, "message": str(res)})
                tool_trace.append({"error": str(res)})
                continue
            # Keep full rows for synthesis; strip _* for next planner prompt
            if res.get("tool") == "search_catalog":
                rows = res.pop("_rows", None) or []
                _merge_hits(accumulated, rows)
            elif res.get("tool") == "get_product":
                row = res.pop("_row", None)
                if row:
                    _merge_hits(accumulated, [row])
            elif res.get("tool") == "get_order_status":
                full = res.pop("_order_status", None)
                if isinstance(full, dict):
                    order_status = full
            prior_for_llm.append(res)
            tool_trace.append(
                {
                    "name": res.get("tool"),
                    "ok": res.get("ok"),
                    "count": res.get("count"),
                }
            )

        # If we already have a healthy set of hits, stop early next planner will likely done=true
        if len(accumulated) >= 8:
            # One more planner chance only if rounds remain; otherwise exit
            pass

    # Fallback: if agent found nothing, one last generic search from the user message
    if not accumulated:
        try:
            rows = await execute_search(
                store_id=store_id,
                payload={
                    "search_keywords": (message or "")[:120],
                    "semantic_context": "personalized recommendation from cart and order history",
                    "limit": SEARCH_LIMIT,
                    "filters": {"color": None, "size": None},
                    "rrf_weights": {"keyword_weight": 0.3, "vector_weight": 0.7},
                },
            )
            _merge_hits(accumulated, rows)
            tool_trace.append({"name": "search_catalog", "fallback": True, "count": len(rows)})
        except Exception as e:
            print(f"catalog agent fallback search failed: {e}")

    # Flatten usage
    components: dict[str, dict[str, int]] = {}
    for part in usage_parts:
        components.update(part)

    return {
        "route": "CATALOG_AGENT",
        "search_results": accumulated,
        "order_status": order_status,
        "tool_trace": tool_trace,
        "token_usage_components": components,
    }
