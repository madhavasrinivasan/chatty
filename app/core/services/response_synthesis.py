"""
Response synthesis: LLM single-pass output (LLMSynthesisOutput), variant matching,
and hydration to FinalFrontendResponse with optional Shopify inventory checks.
Uses asyncio.gather for concurrent DB and REST calls; robust try/except in tasks.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.core.config.config import settings
from app.core.schema.schema import (
    FinalFrontendResponse,
    FrontendProductCard,
    LLMSynthesisOutput,
)
from app.core.models.models import store_knowledge

# Optional: GenAI client for LLM call (single pass)
try:
    from google import genai
    _genai_client: genai.Client | None = None

    def _get_genai_client() -> genai.Client:
        global _genai_client
        if _genai_client is None:
            _genai_client = genai.Client(api_key=settings.gemini_api_key)
        return _genai_client
except Exception:
    _get_genai_client = None  # type: ignore

MODEL = "gemini-2.5-flash"


async def get_variant_data_from_db(
    product_id: str,
    store_id: int | None = None,
) -> dict[str, Any]:
    """
    Look up a product's handle, image_url, and variants from store_knowledge.

    Resolution order:
    - If product_id is numeric: treat as store_knowledge.id (optionally scoped by store_id).
    - Else: look up a product row by shopify_product_id, then by handle (scoped by store_id).

    Returns a lightweight dict:
    - handle: str
    - currency: str (placeholder, currently always "USD")
    - image_url: str
    - variants: list[dict]
    - title: str
    - price: str
    """
    try:
        row = None

        # 1) Numeric primary key path
        if product_id.isdigit():
            q = store_knowledge.filter(id=int(product_id))
            if store_id is not None:
                q = q.filter(store_id=store_id)
            row = await q.first()

        # 2) Fallback: Shopify id / handle for products
        if row is None:
            q = store_knowledge.filter(data_type="product")
            if store_id is not None:
                q = q.filter(store_id=store_id)
            row = await q.filter(shopify_product_id=product_id).first()
            if row is None:
                row = await q.filter(handle=product_id).first()

        if row is None:
            return {}

        variants = getattr(row, "variant_data", None) or []
        if isinstance(variants, str):
            try:
                variants = json.loads(variants)
            except json.JSONDecodeError:
                variants = []
        if not isinstance(variants, list):
            variants = []

        return {
            "handle": getattr(row, "handle", "") or "",
            "currency": "USD",
            "image_url": getattr(row, "image_url", None) or "",
            "variants": variants,
            "title": getattr(row, "title", "") or "",
            "price": str(getattr(row, "price", "") or ""),
        }
    except Exception:
        return {}


async def check_shopify_inventory_rest(
    variant_id: str,
    shop_domain: str,
    access_token: str,
) -> bool:
    """
    Async GET to Shopify Admin API to check variant inventory.
    GET /admin/api/2024-01/variants/{id}.json (or 2026-01 if configured).
    Returns True if in stock, False otherwise. Robust to errors (returns False).
    """
    if not variant_id or not shop_domain or not access_token:
        return False
    host = shop_domain.replace("https://", "").replace("http://", "").split("/")[0]
    version = getattr(settings, "shopify_api_version", None) or "2024-01"
    url = f"https://{host}/admin/api/{version}/variants/{variant_id}.json"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={
                    "X-Shopify-Access-Token": access_token,
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
        if resp.status_code != 200:
            return False
        data = resp.json()
        variant = data.get("variant") or data
        # Shopify variant can have inventory_quantity; if > 0 consider in stock
        qty = variant.get("inventory_quantity")
        if qty is not None:
            return int(qty) > 0
        return True
    except Exception:
        return False


def find_best_variant_match(
    requested_options: list[str],
    db_variants_array: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Case-insensitive match of requested_options to variant option1, option2, option3 or title.
    If requested_options is empty, returns the first variant. Returns None if no match.
    """
    if not db_variants_array:
        return None
    requested = [s.strip().lower() for s in (requested_options or []) if s and str(s).strip()]
    if not requested:
        return db_variants_array[0] if db_variants_array else None
    for v in db_variants_array:
        if not isinstance(v, dict):
            continue
        # Build list of variant option values and title for matching
        opts: list[str] = []
        for key in ("option1", "option2", "option3"):
            val = v.get(key)
            if val and str(val).strip():
                opts.append(str(val).strip().lower())
        title_val = v.get("title")
        if title_val and str(title_val).strip():
            opts.append(str(title_val).strip().lower())
        # Every requested option must match at least one variant value
        if all(
            any(req in o or o in req for o in opts)
            for req in requested
        ):
            return v
    return db_variants_array[0] if db_variants_array else None


async def generate_final_response(
    user_query: str,
    hybrid_results: list[dict[str, Any]],
    shop_domain: str,
    db_session: Any,
    access_token: str = "",
    store_id: int | None = None,
) -> FinalFrontendResponse:
    print(f"generate_final_response: {user_query}")
    """
    Top-to-bottom flow:
    A) Single LLM call forcing LLMSynthesisOutput.
    B) Build FinalFrontendResponse from LLM; if selected_products empty, return immediately.
    C) Concurrent get_variant_data_from_db + find_best_variant_match per product.
    D) Concurrent check_shopify_inventory_rest; only in-stock items go to products.
        If all selected products end up omitted (OOS or match failed), append silent rewrite to general_answer.
    """
    # Step A: LLM call (single pass) -> LLMSynthesisOutput
    context = ""
    if hybrid_results:
        context = json.dumps(
            [
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "content": (r.get("content") or "")[:500],
                    "price": r.get("price"),
                    "url": r.get("url"),
                    "image_url": r.get("image_url"),
                }
                for r in hybrid_results[:20]
            ],
            indent=2,
            default=str,
        )
    prompt = f"""You are an e-commerce assistant. The user asked: "{user_query}".

Search results from our catalog (use these to answer and to pick products):
{context}

Output a JSON object with this exact shape. Use empty lists [] when not relevant.
- general_answer: Markdown answer. Use safe phrasing when recommending products (e.g. "You might like…", "Here are some options…").
- urls: List of policy/sizing/collection URLs to show. Empty [] if none.
- selected_products: List of {{ "product_id": "<id from results>", "requested_options": [] or e.g. ["Black","XL"] }}. Empty [] if not product-related.
- suggested_actions: 2-3 short follow-up questions for the UI.

Output ONLY valid JSON, no markdown code block."""

    llm_output: LLMSynthesisOutput
    try:
        if _get_genai_client is None:
            raise RuntimeError("GenAI not available")
        client = _get_genai_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": LLMSynthesisOutput.model_json_schema(),
            },
        )
        raw = getattr(response, "text", None) or str(response)
        data = json.loads(raw)
        llm_output = LLMSynthesisOutput(
            general_answer=data.get("general_answer", ""),
            urls=data.get("urls") or [],
            selected_products=data.get("selected_products") or [],
            suggested_actions=data.get("suggested_actions") or [],
        )
    except Exception:
        llm_output = LLMSynthesisOutput(
            general_answer="I couldn't process that. Please try rephrasing.",
            urls=[],
            selected_products=[],
            suggested_actions=["What products do you have?", "Do you have a size guide?"],
        )

    final = FinalFrontendResponse(
        general_answer=llm_output.general_answer,
        urls=llm_output.urls,
        products=[],
        suggested_actions=llm_output.suggested_actions,
    )
    print(f"llm_output: {llm_output}")
    if not llm_output.selected_products:
        return final

    # Step C: Concurrent DB fetch + variant match
    async def fetch_one(sel: Any) -> tuple[Any, dict | None, dict | None]:
        print(f"fetch_one: {sel}")
        pid = getattr(sel, "product_id", None) or (sel.get("product_id") if isinstance(sel, dict) else None)
        opts = getattr(sel, "requested_options", None) or (sel.get("requested_options") if isinstance(sel, dict) else []) or []
        if not pid:
            return sel, None, None
        try:
            row = await get_variant_data_from_db(str(pid), store_id)
        except Exception:
            row = {}
        if not row:
            return sel, None, None
        variants = row.get("variants") or []
        matched = find_best_variant_match(opts, variants)
        return sel, row, matched

    tasks_c = [fetch_one(sel) for sel in llm_output.selected_products]
    print(f"tasks_c: {tasks_c}")
    results_c = await asyncio.gather(*tasks_c, return_exceptions=True)

    resolved: list[tuple[str, str, str, str, str, str, str]] = []
    for r in results_c:
        if isinstance(r, Exception):
            continue
        sel, row, matched = r
        if not row or not matched:
            continue
        variant_id = str(matched.get("id") or matched.get("variant_id") or "")
        price = str(matched.get("price") or row.get("price") or "")
        title = row.get("title") or ""
        handle = row.get("handle") or ""
        image_url = row.get("image_url") or ""
        currency = row.get("currency") or "USD"
        pid = getattr(sel, "product_id", None) or (sel.get("product_id") if isinstance(sel, dict) else "")
        resolved.append((str(pid), variant_id, title, price, currency, handle, image_url))

    print(f"resolved: {resolved}")
    if not resolved:
        if llm_output.selected_products:
            final.general_answer = (
                final.general_answer
                + "\n\n*Update: I just checked our live inventory and it looks like all of those specific items actually just sold out! Please check the product pages for other available options.*"
            )
        return final

    async def check_one(item: tuple) -> FrontendProductCard | None:
        pid, variant_id, title, price, currency, handle, image_url = item
        try:
            in_stock = await check_shopify_inventory_rest(
                variant_id, shop_domain, access_token
            ) if (shop_domain and access_token) else True
        except Exception:
            in_stock = False
        if not in_stock:
            return None
        return FrontendProductCard(
            product_id=pid,
            variant_id=variant_id,
            title=title,
            price=price,
            currency=currency,
            image_url=image_url or "",
            handle=handle,
            in_stock=True,
        )

    tasks_d = [check_one(item) for item in resolved]

    products_d = await asyncio.gather(*tasks_d, return_exceptions=True)
    print(f"products_d: {products_d}")

    for p in products_d:
        if isinstance(p, FrontendProductCard):
            final.products.append(p)
        elif isinstance(p, dict):
            try:
                final.products.append(FrontendProductCard(**p))
            except Exception:
                pass

    # Silent rewrite: all selected were OOS or failed
    if llm_output.selected_products and not final.products:
        final.general_answer = (
            final.general_answer
            + "\n\n*Update: I just checked our live inventory and it looks like all of those specific items actually just sold out! Please check the product pages for other available options.*"
        )
    return final
