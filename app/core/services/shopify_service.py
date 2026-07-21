from __future__ import annotations

import base64
import binascii
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, List

import httpx

import shopify
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

from app.core.config.config import settings
from app.core.services.token_tracker import estimate_tokens  # noqa: E402


def _graphql_node_id(node: Any) -> str | None:
    """
    Extract Shopify GID from GraphQL `nodes { id }` responses.
    Each node is a dict like {"id": "gid://shopify/Product/123"}, not a bare string.
    """
    if isinstance(node, dict):
        nid = node.get("id")
        if nid is not None and str(nid).strip():
            return str(nid).strip()
    elif node is not None:
        s = str(node).strip()
        if s:
            return s
    return None


def _parse_discount_minimum_subtotal(disc: dict) -> dict[str, Any] | None:
    """Parse `minimumRequirement { ... DiscountMinimumSubtotal }` when present."""
    mr = disc.get("minimumRequirement")
    if not isinstance(mr, dict):
        return None
    gt = mr.get("greaterThanOrEqualToSubtotal")
    if not isinstance(gt, dict):
        return None
    raw_amt = gt.get("amount")
    try:
        amount = float(raw_amt) if raw_amt is not None else None
    except (TypeError, ValueError):
        amount = None
    cc = gt.get("currencyCode")
    if amount is None and not (cc and str(cc).strip()):
        return None
    return {"amount": amount, "currency_code": str(cc).strip() if cc else None}


def generate_shopify_install_url(store_name: str) -> tuple[str, str]:
    """
    Build the Shopify app install URL for a given store.

    Returns (install_url, state).
    """
    clean_store = store_name.replace("https://", "").replace(".myshopify.com", "").strip()
    scopes = [
        "read_products",
        "read_content",
        "read_orders",
        "read_inventory",
        "read_discounts",
        "write_products",
        "read_locations",
        "read_returns",   # required for returnableFulfillments query (return-eligible items)
        "write_returns",  # required for returnRequest mutation (create return)
    ]
    callback_domain = (settings.shopify_callback_domain or "").rstrip("/")
    redirect_uri = f"{callback_domain}" if callback_domain else ""
    state = binascii.b2a_hex(os.urandom(15)).decode("utf-8")
    session = shopify.Session(f"{clean_store}.myshopify.com", settings.shopify_api_version or "2024-04")
    permission_url = session.create_permission_url(scopes, redirect_uri, state)
    return permission_url, state


def encrypt_token(token: str) -> str:
    """Encrypt a string (e.g. JWT) with AES-256-CBC; returns base64-encoded iv+ciphertext."""
    key = hashlib.sha256((settings.secret_key or "default-secret").encode()).digest()
    iv = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    data = token.encode("utf-8")
    ct = cipher.encrypt(pad(data, AES.block_size))
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt_token(encrypted: str) -> str:
    """Decrypt a string produced by encrypt_token; returns the original JWT/token string."""
    key = hashlib.sha256((settings.secret_key or "default-secret").encode()).digest()
    raw = base64.b64decode(encrypted)
    iv, ct = raw[:16], raw[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ct), AES.block_size).decode("utf-8")


def _normalize_shop_domain(shop_domain: str) -> str:
    host = (shop_domain or "").strip().replace("https://", "").replace("http://", "").split("/")[0]
    if not host:
        return ""
    if host.endswith(".myshopify.com"):
        return host
    # Allow passing "storename" or "storename.myshopify.com"
    return f"{host.split('.')[0]}.myshopify.com"


# ---------------------------------------------------------------------------
# Admin GraphQL transport helpers
#
# All Admin GraphQL callers share the same endpoint/header/response handling.
# These helpers keep the individual query functions focused on their query text
# and result shaping instead of repeating httpx + error-checking boilerplate.
# ---------------------------------------------------------------------------


def _admin_graphql_endpoint(host: str, version: str) -> str:
    return f"https://{host}/admin/api/{version}/graphql.json"


def _admin_headers(access_token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }


def _graphql_data_from_response(resp: httpx.Response, *, context: str = "") -> dict[str, Any] | None:
    """
    Validate an Admin GraphQL HTTP response and return its `data` object.

    Returns None on HTTP errors or GraphQL `errors`; returns {} when the request
    succeeded but carried no data. When `context` is set, failures are logged.
    """
    if resp.status_code != 200:
        if context:
            print(f"{context} HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    payload = resp.json() if resp.content else {}
    if not isinstance(payload, dict):
        return None
    if payload.get("errors"):
        if context:
            print(f"{context} GraphQL errors: {payload.get('errors')}")
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


async def _admin_graphql(
    host: str,
    access_token: str,
    query: str,
    variables: dict[str, Any],
    *,
    version: str,
    timeout_s: float = 10.0,
    context: str = "",
) -> dict[str, Any] | None:
    """Execute an Admin GraphQL query asynchronously; returns the `data` object or None."""
    url = _admin_graphql_endpoint(host, version)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                url, json={"query": query, "variables": variables}, headers=_admin_headers(access_token)
            )
        return _graphql_data_from_response(resp, context=context)
    except Exception as e:
        if context:
            print(f"{context} error: {e}")
        return None


def _admin_graphql_sync(
    host: str,
    access_token: str,
    query: str,
    variables: dict[str, Any],
    *,
    version: str,
    timeout_s: float = 20.0,
    context: str = "",
) -> dict[str, Any] | None:
    """Execute an Admin GraphQL query synchronously; returns the `data` object or None."""
    url = _admin_graphql_endpoint(host, version)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(
                url, json={"query": query, "variables": variables}, headers=_admin_headers(access_token)
            )
        return _graphql_data_from_response(resp, context=context)
    except Exception as e:
        if context:
            print(f"{context} error: {e}")
        return None


def _collect_node_ids(block: Any) -> list[str]:
    """Extract GIDs from a `{ nodes { id } }` connection block."""
    if not isinstance(block, dict):
        return []
    ids: list[str] = []
    for node in block.get("nodes") or []:
        nid = _graphql_node_id(node)
        if nid:
            ids.append(nid)
    return ids


# ---------------------------------------------------------------------------
# Reusable discount GraphQL fragments
#
# The discount union types repeat the same value/items/minimum-requirement
# selections, so we define them once and compose the per-type members.
# ---------------------------------------------------------------------------

_DISCOUNT_CODES_FRAGMENT = "codes(first: 5) { nodes { code } }"

_DISCOUNT_VALUE_FRAGMENT = """
value {
  __typename
  ... on DiscountPercentage { percentage }
  ... on DiscountAmount { amount { amount currencyCode } }
}
""".strip()

_DISCOUNT_ITEMS_FRAGMENT = """
items {
  ... on DiscountProducts { products(first: 50) { nodes { id } } }
  ... on DiscountCollections { collections(first: 50) { nodes { id } } }
}
""".strip()

_DISCOUNT_MIN_SUBTOTAL_FRAGMENT = """
minimumRequirement {
  ... on DiscountMinimumSubtotal {
    greaterThanOrEqualToSubtotal { amount currencyCode }
  }
}
""".strip()

_DISCOUNT_CUSTOMER_GETS_FRAGMENT = f"customerGets {{ {_DISCOUNT_VALUE_FRAGMENT} {_DISCOUNT_ITEMS_FRAGMENT} }}"
_DISCOUNT_CUSTOMER_BUYS_FRAGMENT = f"customerBuys {{ {_DISCOUNT_ITEMS_FRAGMENT} }}"


def _discount_nodes_query(members: list[str]) -> str:
    """Wrap discount union member selections in the standard `discountNodes` query."""
    body = "\n".join(members)
    return (
        "query DiscountNodes($first: Int!, $query: String!) {\n"
        "  discountNodes(first: $first, query: $query) {\n"
        "    edges {\n"
        "      node {\n"
        "        id\n"
        "        discount {\n"
        "          __typename\n"
        f"{body}\n"
        "        }\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}"
    )


def _discount_summary_query() -> str:
    """Lightweight query: merchant-facing title/summary/status only (no entitlements)."""
    basic = "title summary status"
    app = "title status"
    members = [
        f"... on DiscountCodeBasic {{ {basic} }}",
        f"... on DiscountAutomaticBasic {{ {basic} }}",
        f"... on DiscountCodeBxgy {{ {basic} }}",
        f"... on DiscountAutomaticBxgy {{ {basic} }}",
        f"... on DiscountCodeFreeShipping {{ {basic} }}",
        f"... on DiscountAutomaticFreeShipping {{ {basic} }}",
        f"... on DiscountAutomaticApp {{ {app} }}",
        f"... on DiscountCodeApp {{ {app} }}",
    ]
    return _discount_nodes_query(members)


def _active_code_discounts_query() -> str:
    """Rich query: codes, value, and product/collection entitlements per discount type."""
    common = "title status summary"
    codes = _DISCOUNT_CODES_FRAGMENT
    gets = _DISCOUNT_CUSTOMER_GETS_FRAGMENT
    buys = _DISCOUNT_CUSTOMER_BUYS_FRAGMENT
    minsub = _DISCOUNT_MIN_SUBTOTAL_FRAGMENT
    members = [
        f"... on DiscountCodeBasic {{ {common} {codes} {gets} }}",
        f"... on DiscountCodeBxgy {{ {common} {codes} {buys} {gets} }}",
        f"... on DiscountCodeFreeShipping {{ {common} {codes} {minsub} }}",
        f"... on DiscountAutomaticBasic {{ {common} {gets} }}",
        f"... on DiscountAutomaticBxgy {{ {common} {buys} {gets} }}",
        f"... on DiscountAutomaticFreeShipping {{ {common} {minsub} }}",
    ]
    return _discount_nodes_query(members)


async def fetch_active_discounts_summary(
    shop_domain: str,
    access_token: str,
    *,
    first: int = 25,
    timeout_s: float = 10.0,
) -> str:
    """
    Fetch ACTIVE discounts from Shopify via GraphQL Admin API `discountNodes`.

    Notes:
    - Requires the app to have the `read_discounts` scope and a token authorized with that scope.
    - Intentionally does NOT fetch the actual discount codes; we only surface merchant-facing titles/summaries.
    """
    host = _normalize_shop_domain(shop_domain)
    if not host or not (access_token or "").strip():
        return ""
    version = getattr(settings, "shopify_api_version", None) or "2026-01"

    variables = {"first": int(first), "query": "status:active"}
    try:
        data = await _admin_graphql(
            host,
            access_token,
            _discount_summary_query(),
            variables,
            version=version,
            timeout_s=timeout_s,
        )
        if data is None:
            return ""
        edges = (((data.get("discountNodes") or {}).get("edges")) or [])
        if not isinstance(edges, list) or not edges:
            return ""

        lines: list[str] = []
        for e in edges[: int(first)]:
            node = e.get("node") if isinstance(e, dict) else None
            disc = (node or {}).get("discount") if isinstance(node, dict) else None
            if not isinstance(disc, dict):
                continue
            title = (disc.get("title") or "").strip()
            status = (disc.get("status") or "").strip()
            summary = (disc.get("summary") or "").strip()
            dtype = (disc.get("__typename") or "").strip()
            if not title and not summary:
                continue
            parts = []
            if title:
                parts.append(title)
            if summary:
                parts.append(summary)
            if dtype:
                parts.append(f"type={dtype}")
            if status:
                parts.append(f"status={status}")
            lines.append("- " + " • ".join(parts))

        if not lines:
            return ""
        return "ACTIVE DISCOUNTS:\n" + "\n".join(lines)
    except Exception:
        return ""


async def get_active_discounts(
    shop_domain: str,
    access_token: str,
    *,
    first: int = 50,
    timeout_s: float = 10.0,
) -> list[dict[str, Any]]:
    """
    Tool-style helper: fetch ACTIVE code-based discounts plus their basic entitlements.

    Returns a list of dicts shaped like:
    {
        "code": "SAVE10",
        "title": "...",
        "type": "percentage" | "amount" | "free_shipping" | "bxgy" | "app",
        "value": 10.0,
        "currency": "USD" | null,
        "entitled_product_ids": ["gid://shopify/Product/123", ...],  # GIDs from node.id
        "entitled_collection_ids": ["gid://shopify/Collection/456", ...],
        "trigger_product_ids": [...], "trigger_collection_ids": [...],  # BXGY customerBuys
        "minimum_subtotal": {"amount": float, "currency_code": str} | omitted,  # free shipping thresholds
    }
    """
    host = _normalize_shop_domain(shop_domain)
    if not host or not (access_token or "").strip():
        return []
    version = getattr(settings, "shopify_api_version", None) or "2026-01"

    variables = {"first": int(first), "query": "status:active"}
    try:
        data = await _admin_graphql(
            host,
            access_token,
            _active_code_discounts_query(),
            variables,
            version=version,
            timeout_s=timeout_s,
        )
        if data is None:
            return []
        edges = (((data.get("discountNodes") or {}).get("edges")) or [])
        if not isinstance(edges, list):
            return []

        out: list[dict[str, Any]] = []
        for e in edges[: int(first)]:
            node = e.get("node") if isinstance(e, dict) else None
            disc = (node or {}).get("discount") if isinstance(node, dict) else None
            if not isinstance(disc, dict):
                continue
            dtype = str(disc.get("__typename") or "")
            title = str(disc.get("title") or "").strip()
            # Be forgiving: some union variants may omit status in practice.
            status = str(disc.get("status") or "").strip().upper()
            if status and status != "ACTIVE":
                continue
            # Codes exist only for code-based discounts; automatic discounts have none.
            codes: list[str | None] = []
            codes_block = disc.get("codes")
            if isinstance(codes_block, dict):
                code_nodes = codes_block.get("nodes") or []
                for cn in code_nodes:
                    c = (cn or {}).get("code")
                    if c and str(c).strip():
                        codes.append(str(c).strip())
            if not codes:
                # For automatic discounts, we still want one logical entry (code=None).
                codes = [None]

            customer_gets = disc.get("customerGets") or {}
            value = customer_gets.get("value") or {}
            v_type = str(value.get("__typename") or "")
            amount_value: float | None = None
            currency: str | None = None
            logical_type = "app"
            if v_type == "DiscountPercentage":
                try:
                    amount_value = float(value.get("percentage") or 0.0)
                except (TypeError, ValueError):
                    amount_value = None
                logical_type = "percentage"
            elif v_type == "DiscountAmount":
                amount = (value.get("amount") or {}) if isinstance(value.get("amount"), dict) else value.get("amount")
                if isinstance(amount, dict):
                    try:
                        amount_value = float(amount.get("amount") or 0.0)
                    except (TypeError, ValueError):
                        amount_value = None
                    currency = str(amount.get("currencyCode") or "") or None
                logical_type = "amount"
            elif "FreeShipping" in dtype:
                logical_type = "free_shipping"

            minimum_subtotal = _parse_discount_minimum_subtotal(disc)

            # AllDiscountItems -> applies to all products; leave entitlements empty to signal global.
            items = customer_gets.get("items") or {}
            entitled_products = _collect_node_ids(items.get("products") if isinstance(items, dict) else None)
            entitled_collections = _collect_node_ids(items.get("collections") if isinstance(items, dict) else None)

            # BXGY trigger side: what the customer must buy (for upsell targeting).
            buys_items = (disc.get("customerBuys") or {}).get("items") or {}
            trigger_products = _collect_node_ids(buys_items.get("products") if isinstance(buys_items, dict) else None)
            trigger_collections = _collect_node_ids(buys_items.get("collections") if isinstance(buys_items, dict) else None)

            for code in codes:
                row: dict[str, Any] = {
                    "code": code,
                    "title": title,
                    "type": logical_type,
                    "value": amount_value,
                    "currency": currency,
                    "entitled_product_ids": entitled_products,
                    "entitled_collection_ids": entitled_collections,
                    "trigger_product_ids": trigger_products,
                    "trigger_collection_ids": trigger_collections,
                }
                if minimum_subtotal is not None:
                    row["minimum_subtotal"] = minimum_subtotal
                out.append(row)
        return out
    except Exception:
        return []

def get_product_collections(product_id) -> List[str]:
    """
    Fetch the names of all collections (Manual & Automated)
    that a specific product belongs to.
    """
    collection_names: List[str] = []

    # Custom collections
    try:
        custom_colls = shopify.CustomCollection.find(product_id=product_id)
        if custom_colls:
            items = custom_colls if isinstance(custom_colls, list) else [custom_colls]
            collection_names.extend([str(c.title) for c in items if getattr(c, "title", None)])
    except Exception:
        pass

    # Smart collections
    try:
        smart_colls = shopify.SmartCollection.find(product_id=product_id)
        if smart_colls:
            items = smart_colls if isinstance(smart_colls, list) else [smart_colls]
            collection_names.extend([str(c.title) for c in items if getattr(c, "title", None)])
    except Exception:
        pass

    # Remove duplicates
    return list(set(collection_names))


def transform_shopify_product(raw_json: dict, collection_text: str = "") -> dict:
    """
    Flatten raw Shopify product JSON into a StoreKnowledge-friendly shape.
    """
    # 1. Clean the HTML description
    soup = BeautifulSoup((raw_json.get("body_html") or ""), "html.parser")
    clean_description = soup.get_text(separator=" ").strip()

    # 2. Aggregate variants (price, stock, options)
    variants = raw_json.get("variants") or []

    prices: list[float] = []
    total_stock = 0
    for v in variants:
        if not isinstance(v, dict):
            continue
        price_val = v.get("price")
        if price_val is not None:
            try:
                prices.append(float(price_val))
            except (TypeError, ValueError):
                pass
        qty = v.get("inventory_quantity")
        if isinstance(qty, (int, float)):
            total_stock += int(qty)

    min_price = min(prices) if prices else 0.0

    # Options text (e.g. colors/sizes)
    option_values: list[str] = []
    for option in raw_json.get("options") or []:
        if not isinstance(option, dict):
            continue
        vals = option.get("values") or []
        option_values.extend([str(v) for v in vals])
    options_text = ", ".join(option_values)

    # Collect SKUs from variants (for "model number" / SKU search)
    skus: list[str] = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        sku = v.get("sku")
        if sku and str(sku).strip():
            sku_str = str(sku).strip()
            if sku_str not in skus:
                skus.append(sku_str)
    skus_text = ", ".join(skus) if skus else ""

    # 3. Build the content blob the embedding model will see (includes collections for AI search)
    title = raw_json.get("title") or ""
    handle = raw_json.get("handle") or ""
    tags = raw_json.get("tags") or ""
    vendor = (raw_json.get("vendor") or "").strip()

    parts = [title]
    if clean_description:
        parts.append(clean_description)
    if vendor:
        parts.append(f"Vendor: {vendor}.")
    if options_text:
        parts.append(f"Available Options: {options_text}.")
    if skus_text:
        parts.append(f"SKUs: {skus_text}.")
    parts.append(f"Collections: {collection_text if collection_text else 'None'}.")
    if tags:
        parts.append(f"Tags: {tags}")
    content_blob = " ".join(parts).strip()

    # 4. Content hash for change detection
    content_hash = hashlib.md5(content_blob.encode("utf-8")).hexdigest()

    # 5. First image URL (if any)
    image_url = None
    images = raw_json.get("images") or []
    if images:
        first = images[0] or {}
        if isinstance(first, dict):
            image_url = first.get("src")

    # 6. Return flattened row
    row = {
        "shopify_product_id": str(raw_json.get("id") or ""),
        "handle": handle,
        "title": title,
        "content": content_blob,
        "price": min_price,
        "stock": total_stock,
        "image_url": image_url,
        "variant_data": variants,
        "content_hash": content_hash,
    }
    if collection_text:
        row["collections"] = collection_text
    return row


def _fetch_order_detail_json(
    host: str,
    version: str,
    access_token: str,
    order_numeric_id: int | str,
) -> dict[str, Any] | None:
    """GET /orders/{id}.json — full line_items + fulfillments + tracking."""
    url = f"https://{host}/admin/api/{version}/orders/{order_numeric_id}.json"
    headers = {"X-Shopify-Access-Token": access_token}
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"_fetch_order_detail_json HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            return None
        order = data.get("order")
        return order if isinstance(order, dict) else None
    except Exception as e:
        print(f"_fetch_order_detail_json error: {e}")
        return None


def _relative_ship_phrase(iso_dt: str | None) -> str:
    """Human timing: 'today', 'yesterday', or 'Jan 15, 2025'."""
    if not iso_dt:
        return ""
    try:
        s = str(iso_dt).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = dt.astimezone(timezone.utc).date()
        today = datetime.now(timezone.utc).date()
        if d == today:
            return "today"
        if d == today - timedelta(days=1):
            return "yesterday"
        return dt.astimezone(timezone.utc).strftime("%b %d, %Y")
    except Exception:
        return ""


def _line_items_from_order_dict(order: dict[str, Any]) -> list[dict[str, Any]]:
    raw = order.get("line_items") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for li in raw:
        if not isinstance(li, dict):
            continue
        title = (li.get("name") or li.get("title") or "").strip()
        qty = int(li.get("quantity") or 0)
        variant = (li.get("variant_title") or "").strip()
        line = f"{title}"
        if variant:
            line = f"{title} ({variant})"
        out.append(
            {
                "title": title,
                "quantity": qty,
                "variant_title": variant,
                "sku": (li.get("sku") or "").strip(),
                "display_line": f"{line} × {qty}" if qty else line,
            }
        )
    return out


def _shipping_payload_from_order_dict(order: dict[str, Any]) -> dict[str, Any]:
    """Fulfillments + tracking + shipped date hints for UI and prompts."""
    fulfillments_raw = order.get("fulfillments") or []
    if not isinstance(fulfillments_raw, list):
        fulfillments_raw = []
    fulfillments: list[dict[str, Any]] = []
    summary_lines: list[str] = []
    for f in fulfillments_raw:
        if not isinstance(f, dict):
            continue
        status = (f.get("status") or "").strip()
        company = (f.get("tracking_company") or "").strip()
        tracking = (f.get("tracking_number") or "").strip()
        if not tracking:
            tns = f.get("tracking_numbers")
            if isinstance(tns, list) and tns:
                tracking = str(tns[0])
        shipped_at = f.get("shipped_at") or f.get("created_at")
        when = _relative_ship_phrase(str(shipped_at) if shipped_at else "")
        when_label = ""
        if when == "today":
            when_label = "Shipped today"
        elif when == "yesterday":
            when_label = "Shipped yesterday"
        elif when:
            when_label = f"Shipped on {when}"
        parts = [p for p in [when_label, f"{company}" if company else None, f"Tracking: {tracking}" if tracking else None, status or None] if p]
        line = " · ".join(parts)
        if line:
            summary_lines.append(line)
        fulfillments.append(
            {
                "status": status or None,
                "tracking_company": company or None,
                "tracking_number": tracking or None,
                "shipped_at": str(shipped_at) if shipped_at else None,
                "shipped_relative": when or None,
                "summary_line": line or None,
            }
        )
    return {
        "fulfillments": fulfillments,
        "summary_lines": summary_lines,
    }


def get_order_status(shop_domain: str, access_token: str, order_id: str) -> dict[str, Any]:
    """
    Fetch order status from Shopify by order name/number (e.g. "#1001" or "1001").
    Loads full order JSON for line items + shipping/fulfillment/tracking.
    Sync; call via asyncio.to_thread from async code.
    """
    print(f"Getting order status for {order_id} from {shop_domain}")
    if not (order_id or "").strip():
        return {"found": False, "message": "Order ID is required", "prompting": True}
    order_id = (order_id or "").strip()
    if not order_id.startswith("#"):
        order_id = "#" + order_id
    host = _normalize_shop_domain(shop_domain)
    if not host:
        return {"found": False, "message": "Invalid shop domain", "order_name": order_id}
    version = settings.shopify_api_version or "2024-01"
    try:
        with shopify.Session.temp(host, version, access_token or ""):
            orders = shopify.Order.find(name=order_id, status="any")
            if not orders:
                return {"found": False, "message": "Order not found", "order_name": order_id}
            order_list = orders if isinstance(orders, list) else list(orders)
            for order in order_list:
                name = getattr(order, "name", None) or ""
                if str(name).strip().lower() != order_id.lower():
                    continue
                oid = getattr(order, "id", None)
                detail: dict[str, Any] | None = None
                if oid is not None and (access_token or "").strip():
                    detail = _fetch_order_detail_json(host, version, access_token, oid)
                if detail:
                    line_items = _line_items_from_order_dict(detail)
                    shipping = _shipping_payload_from_order_dict(detail)
                    return {
                        "found": True,
                        "order_name": detail.get("name") or name,
                        "id": detail.get("id"),
                        "financial_status": detail.get("financial_status"),
                        "fulfillment_status": detail.get("fulfillment_status") or "unfulfilled",
                        "total_price": detail.get("total_price"),
                        "currency": detail.get("currency") or detail.get("presentment_currency"),
                        "created_at": str(detail.get("created_at") or ""),
                        "updated_at": str(detail.get("updated_at") or ""),
                        "line_items": line_items,
                        "shipping": shipping,
                    }
                return {
                    "found": True,
                    "order_name": name,
                    "id": oid,
                    "financial_status": getattr(order, "financial_status", None),
                    "fulfillment_status": getattr(order, "fulfillment_status", None) or "unfulfilled",
                    "total_price": getattr(order, "total_price", None),
                    "created_at": str(getattr(order, "created_at", "")),
                    "updated_at": str(getattr(order, "updated_at", "")),
                    "line_items": [],
                    "shipping": {"fulfillments": [], "summary_lines": []},
                }
            return {"found": False, "message": "Order not found", "order_name": order_id}
    except Exception as e:
        return {"found": False, "message": str(e), "order_name": order_id}


def format_order_support_message(o: dict[str, Any]) -> str:
    """
    Build Markdown for ORDER_SUPPORT from a `get_order_status` payload:
    financial/fulfillment summary, line items, shipping/tracking.
    """
    if not o.get("found"):
        return str(
            o.get("message")
            or "We couldn’t find that order. Please check the number and try again."
        )
    name = o.get("order_name") or ""
    fin = o.get("financial_status") or "—"
    ful = o.get("fulfillment_status") or "—"
    total = o.get("total_price") or "—"
    cur = (o.get("currency") or "").strip()
    price_bits = f"**Payment:** {fin} · **Fulfillment:** {ful} · **Order total:** {total}"
    if cur:
        price_bits += f" {cur}"
    lines: list[str] = [
        f"Here’s the latest on **{name}**.",
        f"- {price_bits}",
    ]
    items = o.get("line_items") or []
    if isinstance(items, list) and items:
        lines.append("\n**Products in this order:**")
        for li in items[:40]:
            if isinstance(li, dict):
                dl = (li.get("display_line") or li.get("title") or "").strip()
                if dl:
                    lines.append(f"  - {dl}")
    ship = o.get("shipping") or {}
    summary = ship.get("summary_lines") if isinstance(ship, dict) else None
    if isinstance(summary, list) and summary:
        lines.append("\n**Shipping & tracking:**")
        for s in summary:
            if s:
                lines.append(f"  - {s}")
    elif isinstance(ship, dict):
        fulf = ship.get("fulfillments") or []
        if not fulf and ful:
            lines.append(
                f"\n**Shipping:** {ful} (no tracking yet — your package may still be preparing)."
            )
    return "\n".join(lines)


def _order_email_matches_rest(order: Any, email_lower: str) -> bool:
    """Match Shopify REST order to customer email (order.email, contact_email, or nested customer.email)."""
    if not email_lower:
        return False
    candidates: list[str] = []
    for attr in ("email", "contact_email"):
        v = getattr(order, attr, None)
        if v:
            candidates.append(str(v).strip().lower())
    customer = getattr(order, "customer", None)
    if customer is not None:
        ce = getattr(customer, "email", None)
        if ce:
            candidates.append(str(ce).strip().lower())
    return email_lower in candidates


def _get_orders_by_email_graphql(
    host: str,
    access_token: str,
    email_lower: str,
    limit: int,
) -> list[dict[str, Any]] | None:
    """
    Use Admin GraphQL `orders(query: "email:...")` so we search the full order index, not only the last N orders.
    Returns None if the request fails (caller may use REST fallback).
    """
    version = settings.shopify_api_version or "2024-01"
    # https://shopify.dev/docs/api/usage/search-syntax — quote so +tags and odd chars match
    safe_email = email_lower.replace("\\", "\\\\").replace('"', '\\"')
    search_query = f'email:"{safe_email}"'
    gql = """
    query OrdersByEmail($first: Int!, $query: String!) {
      orders(first: $first, query: $query, sortKey: CREATED_AT, reverse: true) {
        edges {
          node {
            name
            email
            lineItems(first: 15) {
              edges {
                node {
                  title
                }
              }
            }
          }
        }
      }
    }
    """.strip()
    first = min(max(limit, 1), 50)
    variables = {"first": first, "query": search_query}
    try:
        data = _admin_graphql_sync(
            host,
            access_token,
            gql,
            variables,
            version=version,
            context="get_orders_by_email_graphql",
        )
        if data is None:
            return None
        edges = (((data.get("orders") or {}).get("edges")) or [])
        if not isinstance(edges, list):
            return None
        out: list[dict[str, Any]] = []
        for e in edges:
            if len(out) >= limit:
                break
            node = e.get("node") if isinstance(e, dict) else None
            if not isinstance(node, dict):
                continue
            name = node.get("name") or ""
            li_edges = (((node.get("lineItems") or {}).get("edges")) or [])
            titles: list[str] = []
            if isinstance(li_edges, list):
                for le in li_edges:
                    n = (le or {}).get("node") if isinstance(le, dict) else None
                    if isinstance(n, dict) and n.get("title"):
                        titles.append(str(n["title"]))
            summary = ", ".join(titles[:10]) if titles else "—"
            out.append({"order_name": str(name), "line_items_summary": summary})
        return out
    except Exception as e:
        print(f"get_orders_by_email_graphql error: {e}")
        return None


def _get_orders_by_email_rest_fallback(
    shop: str,
    version: str,
    access_token: str,
    email_lower: str,
    limit: int,
) -> list[dict[str, Any]]:
    """
    Last resort: scan recent orders (up to REST max 250) and match email fields.
    Wrong for busy stores if the customer's orders are older than the scanned window.
    """
    try:
        with shopify.Session.temp(shop, version, access_token or ""):
            orders = shopify.Order.find(limit=250, status="any", order="created_at DESC")
            order_list = orders if isinstance(orders, list) else list(orders)
            result: list[dict[str, Any]] = []
            for order in order_list:
                if len(result) >= limit:
                    break
                if not _order_email_matches_rest(order, email_lower):
                    continue
                order_name = getattr(order, "name", None) or ""
                line_items = getattr(order, "line_items", None) or []
                line_list = line_items if isinstance(line_items, list) else list(line_items)
                titles = [getattr(li, "title", "") or "" for li in line_list[:10]]
                summary = ", ".join(titles) if titles else "—"
                result.append({"order_name": str(order_name), "line_items_summary": summary})
            return result
    except Exception as e:
        print(f"_get_orders_by_email_rest_fallback error: {e}")
        return []


def get_orders_by_customer_email(
    shop_domain: str,
    access_token: str,
    customer_email: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Fetch recent orders for a customer by email. Returns list of dicts with order_name and line_items_summary.

    Uses Admin GraphQL `orders(query: "email:...")` first (correct full-store search). Falls back to scanning
    the last 250 REST orders only if GraphQL fails.
    Sync; call via asyncio.to_thread from async code.
    """
    if not (customer_email or "").strip():
        return []
    email = (customer_email or "").strip().lower()
    host = _normalize_shop_domain(shop_domain)
    if not host or not (access_token or "").strip():
        return []
    version = settings.shopify_api_version or "2024-01"

    gql_rows = _get_orders_by_email_graphql(host, access_token, email, limit)
    if gql_rows is not None:
        return gql_rows

    return _get_orders_by_email_rest_fallback(host, version, access_token, email, limit)


# --- Token estimation: shared implementation in token_tracker ---




def build_store_token_usage_response(
    rows: list[dict[str, Any]],
    *,
    store_id: int,
    max_breakdown: int = 200,
    usd_per_million_input_flash: float = 0.075,
) -> dict[str, Any]:
    """
    Sum estimated tokens over store_knowledge rows (title + content per row).
    `rows` should include keys: id, title, content, data_type (optional).
    """
    total = 0
    breakdown: list[dict[str, Any]] = []
    for row in rows:
        title = row.get("title") or ""
        content = row.get("content") or ""
        text = f"{title} {content}"
        n = estimate_tokens(text)
        total += n
        breakdown.append(
            {
                "id": row.get("id"),
                "data_type": str(row.get("data_type") or ""),
                "title": (str(title)[:120] + "…") if len(str(title)) > 120 else str(title),
                "tokens": n,
            }
        )
    cost = round((total / 1_000_000) * usd_per_million_input_flash, 6)
    return {
        "store_id": store_id,
        "estimated_tokens": total,
        "row_count": len(rows),
        "estimated_cost_usd_gemini_1_5_flash_input": cost,
        "note": "tiktoken cl100k_base estimate; not official Gemini count_tokens",
        "breakdown": breakdown[: max(0, max_breakdown)],
    }


