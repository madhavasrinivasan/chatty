from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.core.config.config import settings
from app.core.config.gemini_client import generate_content_text, get_genai_client
from app.core.schema.schema import (
    FinalFrontendResponse,
    FrontendAction,
    FrontendProductCard,
    LLMSynthesisOutput,
)
from app.core.models.models import store_knowledge
from app.core.services.token_tracker import estimate_tokens

# Optional: GenAI client for LLM call (single pass)
try:
    from google import genai
    _genai_client: genai.Client | None = None

    def _get_genai_client() -> genai.Client:
        global _genai_client
        if _genai_client is None:
            _genai_client = get_genai_client()
        return _genai_client
except Exception:
    _get_genai_client = None  # type: ignore

MODEL = settings.gemini_synthesis_model or "gemini-2.5-flash-lite"


def _results_index(hybrid_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize search rows for LLM context + title→id backfill."""
    out: list[dict[str, Any]] = []
    for r in hybrid_results or []:
        if not isinstance(r, dict):
            continue
        pid = r.get("id")
        shopify_pid = r.get("shopify_product_id")
        title = (r.get("title") or "").strip()
        if pid is None and not shopify_pid:
            continue
        out.append(
            {
                "id": str(pid) if pid is not None else None,
                "shopify_product_id": str(shopify_pid) if shopify_pid else None,
                "title": title,
                "content": (r.get("content") or "")[:500],
                "price": r.get("price"),
                "url": r.get("url"),
                "image_url": r.get("image_url"),
                "discount_info": r.get("discount_info"),
                "handle": r.get("handle"),
            }
        )
    return out


def _backfill_selected_products_from_answer(
    general_answer: str,
    selected_products: list[Any],
    results: list[dict[str, Any]],
    *,
    max_products: int = 5,
) -> list[Any]:
    """
    If the model recommends products in prose but left selected_products empty
    (or incomplete), match titles from search results and fill product cards.
    """
    existing: list[Any] = list(selected_products or [])
    if len(existing) >= max_products or not results:
        return existing

    already: set[str] = set()
    for sel in existing:
        pid = getattr(sel, "product_id", None) or (sel.get("product_id") if isinstance(sel, dict) else None)
        if pid is not None:
            already.add(str(pid))

    answer = (general_answer or "").lower()
    if not answer.strip():
        return existing

    # Prefer longer titles first so "Organic Muslin Bath Towel | ..." beats "Towel"
    ranked = sorted(
        (r for r in results if (r.get("title") or "").strip()),
        key=lambda r: len(r.get("title") or ""),
        reverse=True,
    )
    for r in ranked:
        if len(existing) >= max_products:
            break
        title = (r.get("title") or "").strip()
        if len(title) < 6:
            continue
        if title.lower() not in answer:
            continue
        # Prefer DB id (numeric) for get_variant_data_from_db; fall back to shopify/comez id
        pid = r.get("id") or r.get("shopify_product_id")
        if pid is None or str(pid) in already:
            continue
        already.add(str(pid))
        existing.append({"product_id": str(pid), "requested_options": []})

    return existing


def _filter_product_image_urls(urls: list[Any], results: list[dict[str, Any]]) -> list[str]:
    """Drop product CDN/image URLs mistakenly put in `urls` (those belong on cards)."""
    image_set = {
        str(r.get("image_url") or "").strip().lower()
        for r in results
        if r.get("image_url")
    }
    cleaned: list[str] = []
    for u in urls or []:
        s = str(u or "").strip()
        if not s:
            continue
        low = s.lower()
        if low in image_set:
            continue
        if any(ext in low for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")) and "/images/" in low:
            continue
        cleaned.append(s)
    return cleaned


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
) -> bool | None:
    """
    Async GET to Shopify Admin API to check variant inventory.
    GET /admin/api/2024-01/variants/{id}.json (or 2026-01 if configured).

    Fail-open semantics (we must never fabricate "sold out"):
    - True  -> confirmed in stock (inventory_quantity > 0, or Shopify doesn't track qty).
    - False -> confirmed out of stock (inventory_quantity <= 0).
    - None  -> unknown/unverifiable (missing creds, non-200, timeout, or exception).
    Callers should treat None as showable, since "can't verify" is not "sold out".
    """
    if not variant_id or not shop_domain or not access_token:
        return None
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
            return None
        data = resp.json()
        variant = data.get("variant") or data
        # Shopify variant can have inventory_quantity; if > 0 consider in stock
        qty = variant.get("inventory_quantity")
        if qty is not None:
            return int(qty) > 0
        return True
    except Exception:
        return None


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


def _format_active_chat_for_synthesizer(active_chat_history: list[dict[str, Any]] | None) -> str:
    """Format the full sliced active chat (up to 10 messages) for the synthesizer prompt."""
    if not active_chat_history:
        return ""
    lines = []
    for m in active_chat_history:
        role = (m.get("role") or "user") if isinstance(m, dict) else "user"
        content = (m.get("content") or "") if isinstance(m, dict) else str(m)
        prods = m.get("products") if isinstance(m, dict) else None
        prod_snippet = ""
        if prods and isinstance(prods, list):
            items = []
            for idx, p in enumerate(prods[:5]):
                if isinstance(p, dict):
                    t = p.get("title") or "Item"
                    pid = p.get("product_id") or p.get("id") or ""
                    vid = p.get("variant_id") or ""
                    items.append(f"#{idx+1}: '{t}' (id: {pid}, variant: {vid})")
            if items:
                prod_snippet = f"\n  [Attached products shown in this turn: {', '.join(items)}]"
        if content or prod_snippet:
            lines.append(f"{role}: {content}{prod_snippet}")
    return "\n".join(lines) if lines else ""


async def _resolve_frontend_action(
    llm_action: Any,
    hybrid_results: list[dict[str, Any]],
    frontend_cards: list[FrontendProductCard],
    store_id: int | None = None,
) -> FrontendAction | None:
    if not llm_action:
        return None
    act_type = getattr(llm_action, "type", None) or (llm_action.get("type") if isinstance(llm_action, dict) else None)
    if not act_type:
        return None

    idx = getattr(llm_action, "product_index", None) or (llm_action.get("product_index") if isinstance(llm_action, dict) else None)
    pid = getattr(llm_action, "product_id", None) or (llm_action.get("product_id") if isinstance(llm_action, dict) else None)
    opts = getattr(llm_action, "requested_options", None) or (llm_action.get("requested_options") if isinstance(llm_action, dict) else []) or []
    qty = getattr(llm_action, "quantity", 1) or (llm_action.get("quantity") if isinstance(llm_action, dict) else 1) or 1
    discount_code = getattr(llm_action, "discount_code", None) or (llm_action.get("discount_code") if isinstance(llm_action, dict) else None)

    target_card: FrontendProductCard | None = None
    target_row: dict[str, Any] | None = None

    if isinstance(idx, int) and idx > 0:
        if idx <= len(frontend_cards):
            target_card = frontend_cards[idx - 1]
        elif idx <= len(hybrid_results):
            target_row = hybrid_results[idx - 1]

    if not target_card and not target_row and pid:
        pid_str = str(pid).strip()
        for c in frontend_cards:
            if str(c.product_id).strip() == pid_str or pid_str in str(c.product_id):
                target_card = c
                break
        if not target_card:
            for r in hybrid_results:
                row_pid = str(r.get("id") or r.get("shopify_product_id") or r.get("product_id") or "").strip()
                if row_pid == pid_str or pid_str in row_pid:
                    target_row = r
                    break

    if not target_card and not target_row:
        if len(frontend_cards) == 1:
            target_card = frontend_cards[0]
        elif len(hybrid_results) == 1:
            target_row = hybrid_results[0]

    variant_id: str | None = None
    product_title: str | None = None

    if target_card:
        variant_id = str(target_card.variant_id)
        product_title = target_card.title
    elif target_row:
        product_title = target_row.get("title") or target_row.get("name")
        variant_id = str(target_row.get("variant_id") or "")
        if not variant_id:
            row_pid = str(target_row.get("id") or target_row.get("shopify_product_id") or target_row.get("product_id") or "")
            if row_pid and store_id:
                try:
                    v_row = await get_variant_data_from_db(row_pid, store_id)
                    vars_list = v_row.get("variants") or []
                    matched = find_best_variant_match(opts, vars_list)
                    if matched:
                        variant_id = str(matched.get("id") or matched.get("variant_id") or "")
                except Exception:
                    pass

    code_clean = (discount_code or "").strip() if discount_code else None
    if not code_clean and target_row:
        d_list = target_row.get("discount_info") or []
        if isinstance(d_list, list) and d_list:
            code_clean = d_list[0].get("code")

    final_type = act_type
    if final_type == "AUTO_ADD_TO_CART" and code_clean:
        final_type = "ADD_AND_APPLY_DISCOUNT"

    return FrontendAction(
        type=final_type,
        variant_id=variant_id or None,
        quantity=max(1, int(qty)),
        discount_code=code_clean,
        product_title=product_title,
    )


async def generate_final_response(
    user_query: str,
    hybrid_results: list[dict[str, Any]],
    shop_domain: str,
    db_session: Any,
    access_token: str = "",
    store_id: int | None = None,
    user_facts: str = "",
    order_history: str = "",
    previous_session_history: str = "",
    active_chat_history: list[dict[str, Any]] | None = None,
    cart_items: list[dict[str, Any]] | None = None,
    thinking_budget: int | None = None,
    system_prompt_override: str | None = None,
) -> tuple[FinalFrontendResponse, dict[str, dict[str, int]]]:
    """
    Top-to-bottom flow:
    A) Single LLM call with full context (user_facts, order_history, previous_session_history, entire active chat).
    B) Build FinalFrontendResponse from LLM.
    C) Concurrent get_variant_data_from_db + find_best_variant_match per product.
    D) Concurrent check_shopify_inventory_rest; only in-stock items go to products.
    E) Resolve any requested client actions (AUTO_ADD_TO_CART, APPLY_DISCOUNT).
    """
    print(f"generate_final_response: {user_query}")
    budget = (
        settings.gemini_synthesis_thinking_budget
        if thinking_budget is None
        else int(thinking_budget)
    )
    # Step A: LLM call (single pass) with memory context and full active session
    indexed = _results_index(hybrid_results)
    context = ""
    if indexed:
        # Truncate long descriptions to cut synthesis tokens.
        context = json.dumps(
            [
                {
                    "index": i + 1,
                    "id": r.get("id"),
                    "shopify_product_id": r.get("shopify_product_id"),
                    "title": r.get("title"),
                    "content": ((r.get("content") or "")[:320]),
                    "price": r.get("price"),
                    "url": r.get("url"),
                    "image_url": r.get("image_url"),
                    "discount_info": r.get("discount_info"),
                }
                for i, r in enumerate(indexed[:8])
            ],
            separators=(",", ":"),
            default=str,
        )
    memory_block = ""
    if (user_facts or "").strip() or (order_history or "").strip() or (previous_session_history or "").strip():
        memory_block = (
            "USER FACTS: " + (user_facts or "").strip() + "\n"
            "PAST ORDERS: " + (order_history or "").strip() + "\n"
            "PAST CHATS: " + (previous_session_history or "").strip() + "\n\n"
        )
    active_block = _format_active_chat_for_synthesizer(active_chat_history)
    if active_block:
        memory_block += "ACTIVE SESSION (last 10 messages):\n" + active_block + "\n\n"
    
    if cart_items:
        try:
            cart_str = json.dumps([{"title": i.get("title"), "quantity": i.get("quantity"), "price": i.get("price")} for i in cart_items], separators=(",", ":"))
            memory_block += f"USER'S CURRENT CART (They have these items in their cart right now):\n{cart_str}\n\n"
        except Exception:
            pass

    instruction = (
        "You are a confident, high-performing sales associate for this store. Use the full context "
        "(active session, past chats, facts, orders, and current cart) to sell like a knowledgeable human rep who is trusted with real store data.\n\n"
        "TONE & BEHAVIOR RULES (follow all):\n"
        "1. BE ASSUMPTIVE, NOT PERMISSION-SEEKING. Recommend directly and drive toward the sale. "
        "Say 'Here's the X in your size — I can add it to your cart or you can tap Add to Cart below.'\n"
        "2. ALWAYS CITE EXACT NUMBERS. State exact price with currency and, when a product has a discount, the exact percentage or amount AND the exact code "
        "(e.g. 'It's $48.00, and 15% off right now with code SAVE15'). NEVER use vague phrasing like 'some savings' or 'a discount'.\n"
        "3. ALWAYS END WITH A NEXT STEP. Every general_answer must close with a specific action or question "
        "(e.g. 'Would you like me to add this to your cart?' or 'Should I apply coupon SAVE10 for you?'). Never leave a dead end.\n"
        "4. NEVER CLAIM YOU LACK ACCESS to data that is already provided above. If facts, past orders, the cart, or catalog results are in context, use them confidently. "
        "Never say things like 'I don't have access to that' when the information is present.\n"
        "5. NEVER MENTION STOCK OR AVAILABILITY. Do NOT say an item is in stock, low stock, or sold out, and do NOT add stock disclaimers. "
        "Inventory is verified separately by the system, not by you.\n"
        "6. PERSONALIZE. Acknowledge past conversations when relevant and suggest items based on past orders (e.g., sizing up, complementary items).\n"
        "7. DISCOUNTS & COUPONS:\n"
        "   - If any product in context has discount_info, or if active discounts are present, you MUST cite the exact code and amount/percentage.\n"
        "   - When recommending products with discounts, in suggested_actions offer: 'Apply code <CODE>' and ask in general_answer: 'Want me to apply code <CODE> to your cart?'\n"
        "   - If the user asks to apply a coupon/discount, set action.type to 'APPLY_DISCOUNT' (or 'ADD_AND_APPLY_DISCOUNT') and provide the discount_code.\n"
        "8. AUTO ADD TO CART & PRODUCT ACTIONS:\n"
        "   - Whenever you recommend products, ALWAYS include helpful follow-up chips in suggested_actions like 'Add <Product Name> to cart'.\n"
        "   - If the shopper asks to add an item to their cart (e.g. 'add to cart', 'add the first one', 'add the second product', 'buy the blue one', 'yes add it'), you MUST populate the `action` field!\n"
        "   - Set action.type to 'AUTO_ADD_TO_CART' (or 'ADD_AND_APPLY_DISCOUNT' if a coupon is also specified/available).\n"
        "   - Set action.product_index to the 1-based index (e.g. 1 for first product, 2 for second product) from search results or previous chat turns.\n"
        "   - If requested_options like color/size are specified (e.g. ['Blue', 'L']), put them in action.requested_options.\n"
        "   - If the user's choice is ambiguous, ask a clarifying question in general_answer: 'Which product would you like me to add?' and leave action as null.\n"
        "   - When adding an item, confirm enthusiastically in general_answer: 'I've added the <Product Name> to your cart!'\n"
        "9. PRODUCT CARDS ARE MANDATORY WHEN YOU RECOMMEND. If you name, recommend, or quote a price for any product from "
        "the search results, you MUST include that product in selected_products using the exact `id` field from the results "
        "(string). Never put product image_url values into urls; urls is only for policy/sizing/collection page links.\n\n"
    )
    if system_prompt_override and system_prompt_override.strip():
        instruction += f"MERCHANT STORE CUSTOM RULES & INSTRUCTIONS (strictly respect and adhere to these guidelines):\n{system_prompt_override.strip()}\n\n"
    prompt = f"""{instruction}{memory_block}Current user question: "{user_query}".

Search results from our catalog (use these to answer and to pick products):
{context}

Output a JSON object with this exact shape. Use empty lists [] when not relevant.
- general_answer: Markdown answer written as an assumptive sales rep. Recommend directly, cite exact prices/discount %/codes, and end with a specific next action or question. If adding an item to cart or applying a discount, confirm it here.
- urls: Policy/sizing/collection page links ONLY. Never product image URLs. Empty [] if none.
- selected_products: REQUIRED whenever you recommend catalog products. List of {{ "product_id": "<exact id from results above>", "requested_options": [] or e.g. ["Black","XL"] }}. Max 5. Empty [] ONLY if the answer is not about specific products.
- suggested_actions: 2-3 short follow-up questions or actionable chips for the UI (e.g. "Add <Product Name> to cart", "Apply code <CODE>", "Show size guide").
- action: Optional client action. Fill when shopper wants to add an item to their cart or apply a discount coupon. Otherwise null.
  {{
    "type": "AUTO_ADD_TO_CART" | "APPLY_DISCOUNT" | "ADD_AND_APPLY_DISCOUNT" | null,
    "product_index": 1-based index (e.g. 1 or 2) or null,
    "product_id": string or null,
    "requested_options": ["Blue", "M"] or [],
    "quantity": 1,
    "discount_code": "<CODE>" or null
  }}

Output ONLY valid JSON, no markdown code block."""

    input_tokens = estimate_tokens(prompt)
    llm_output: LLMSynthesisOutput
    output_tokens = 0
    try:
        raw = await asyncio.to_thread(
            generate_content_text,
            MODEL,
            prompt,
            {
                "response_mime_type": "application/json",
                "response_json_schema": LLMSynthesisOutput.model_json_schema(),
                # With thinking OFF (budget 0) a max_output_tokens cap is safe and
                # shortens generation time.  If thinking is enabled (-1 or N) remove
                # this cap because thinking tokens count toward the limit.
                **({
                    "max_output_tokens": 800,
                } if budget == 0 else {}),
                "thinking_config": {"thinking_budget": budget},
            },
        )
        output_tokens = estimate_tokens(raw or "")
        data = json.loads(raw)
        llm_output = LLMSynthesisOutput(
            general_answer=data.get("general_answer", ""),
            urls=_filter_product_image_urls(data.get("urls") or [], indexed),
            selected_products=data.get("selected_products") or [],
            suggested_actions=data.get("suggested_actions") or [],
            action=data.get("action"),
        )
    except Exception:
        llm_output = LLMSynthesisOutput(
            general_answer="I couldn't process that. Please try rephrasing.",
            urls=[],
            selected_products=[],
            suggested_actions=["What products do you have?", "Do you have a size guide?"],
            action=None,
        )

    # Hard backfill: prose recommendations without selected_products still get cards.
    backfilled = _backfill_selected_products_from_answer(
        llm_output.general_answer,
        llm_output.selected_products,
        indexed,
    )
    if len(backfilled) != len(llm_output.selected_products or []):
        print(f"selected_products backfilled: {llm_output.selected_products} -> {backfilled}")
        llm_output = LLMSynthesisOutput(
            general_answer=llm_output.general_answer,
            urls=llm_output.urls,
            selected_products=backfilled,
            suggested_actions=llm_output.suggested_actions,
            action=llm_output.action,
        )

    usage_chunk = {
        "final_response": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    }

    final = FinalFrontendResponse(
        general_answer=llm_output.general_answer,
        urls=llm_output.urls,
        products=[],
        suggested_actions=llm_output.suggested_actions,
        action=None,
    )
    print(f"llm_output: {llm_output}")
    if not llm_output.selected_products:
        final.action = await _resolve_frontend_action(llm_output.action, hybrid_results, final.products, store_id)
        return final, usage_chunk

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
        # Hydration/lookup miss (DB id or variant match failed) — NOT a stock signal.
        # Never fabricate "sold out" here; return the model's answer as-is.
        return final, usage_chunk

    async def check_one(item: tuple) -> FrontendProductCard | None:
        pid, variant_id, title, price, currency, handle, image_url = item
        try:
            in_stock = await check_shopify_inventory_rest(
                variant_id, shop_domain, access_token
            ) if (shop_domain and access_token) else True
        except Exception:
            in_stock = None
        # Fail-open: only drop the card when inventory is CONFIRMED out of stock (False).
        # True (in stock) and None (unverifiable) are both showable.
        if in_stock is False:
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

    # Reaching here with no cards means every resolved product was CONFIRMED out of stock
    # (unverifiable items are kept via fail-open), so this note is honest, not fabricated.
    if llm_output.selected_products and not final.products:
        final.general_answer = (
            final.general_answer
            + "\n\nThose specific options are currently unavailable — want me to find you the closest alternatives in stock?"
        )
    final.action = await _resolve_frontend_action(llm_output.action, hybrid_results, final.products, store_id)
    return final, usage_chunk
