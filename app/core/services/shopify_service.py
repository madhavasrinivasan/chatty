from __future__ import annotations

import base64
import binascii
import hashlib
import os
from typing import Any, List

import httpx

import shopify
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

from app.core.config.config import settings


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
    url = f"https://{host}/admin/api/{version}/graphql.json"

    query = """
    query DiscountNodes($first: Int!, $query: String!) {
      discountNodes(first: $first, query: $query) {
        edges {
          node {
            id
            discount {
              __typename
              ... on DiscountCodeBasic { title summary status }
              ... on DiscountAutomaticBasic { title summary status }
              ... on DiscountCodeBxgy { title summary status }
              ... on DiscountAutomaticBxgy { title summary status }
              ... on DiscountCodeFreeShipping { title summary status }
              ... on DiscountAutomaticFreeShipping { title summary status }
              ... on DiscountAutomaticApp { title status }
              ... on DiscountCodeApp { title status }
            }
          }
        }
      }
    }
    """.strip()

    variables = {"first": int(first), "query": "status:active"}
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json={"query": query, "variables": variables}, headers=headers)
        if resp.status_code != 200:
            return ""
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            return ""
        if payload.get("errors"):
            return ""
        data = payload.get("data") or {}
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
        "entitled_product_ids": ["gid://shopify/Product/123", ...],
        "entitled_collection_ids": ["gid://shopify/Collection/456", ...],
    }
    """
    host = _normalize_shop_domain(shop_domain)
    if not host or not (access_token or "").strip():
        return []
    version = getattr(settings, "shopify_api_version", None) or "2026-01"
    url = f"https://{host}/admin/api/{version}/graphql.json"

    query = """
    query ActiveCodeDiscounts($first: Int!, $query: String!) {
      discountNodes(first: $first, query: $query) {
        edges {
          node {
            id
            discount {
              __typename
              ... on DiscountCodeBasic {
                title
                status
                summary
                codes(first: 5) { nodes { code } }
                customerGets {
                  value {
                    __typename
                    ... on DiscountPercentage { percentage }
                    ... on DiscountAmount { amount { amount currencyCode } }
                  }
                  items {
                    ... on DiscountProducts {
                      products(first: 50) {
                        nodes { id }
                      }
                    }
                    ... on DiscountCollections {
                      collections(first: 50) {
                        nodes { id }
                      }
                    }
                  }
                }
              }
              ... on DiscountCodeBxgy {
                title
                status
                summary
                codes(first: 5) { nodes { code } }
                customerBuys {
                  items {
                    ... on DiscountProducts {
                      products(first: 50) {
                        nodes { id }
                      }
                    }
                    ... on DiscountCollections {
                      collections(first: 50) {
                        nodes { id }
                      }
                    }
                  }
                }
                customerGets {
                  value {
                    __typename
                    ... on DiscountPercentage { percentage }
                    ... on DiscountAmount { amount { amount currencyCode } }
                  }
                  items {
                    ... on DiscountProducts {
                      products(first: 50) {
                        nodes { id }
                      }
                    }
                    ... on DiscountCollections {
                      collections(first: 50) {
                        nodes { id }
                      }
                    }
                  }
                }
              }
              ... on DiscountCodeFreeShipping {
                title
                status
                summary
                codes(first: 5) { nodes { code } }
              }
              ... on DiscountAutomaticBasic {
                title
                status
                summary
                customerGets {
                  value {
                    __typename
                    ... on DiscountPercentage { percentage }
                    ... on DiscountAmount { amount { amount currencyCode } }
                  }
                  items {
                    ... on DiscountProducts {
                      products(first: 50) {
                        nodes { id }
                      }
                    }
                    ... on DiscountCollections {
                      collections(first: 50) {
                        nodes { id }
                      }
                    }
                  }
                }
              }
              ... on DiscountAutomaticBxgy {
                title
                status
                summary
                customerBuys {
                  items {
                    ... on DiscountProducts {
                      products(first: 50) {
                        nodes { id }
                      }
                    }
                    ... on DiscountCollections {
                      collections(first: 50) {
                        nodes { id }
                      }
                    }
                  }
                }
                customerGets {
                  value {
                    __typename
                    ... on DiscountPercentage { percentage }
                    ... on DiscountAmount { amount { amount currencyCode } }
                  }
                  items {
                    ... on DiscountProducts {
                      products(first: 50) {
                        nodes { id }
                      }
                    }
                    ... on DiscountCollections {
                      collections(first: 50) {
                        nodes { id }
                      }
                    }
                  }
                }
              }
              ... on DiscountAutomaticFreeShipping {
                title
                status
                summary
              }
            }
          }
        }
      }
    }
    """.strip()

    variables = {"first": int(first), "query": "status:active"}
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json={"query": query, "variables": variables}, headers=headers)
        if resp.status_code != 200:
            return []
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict) or payload.get("errors"):
            return []
        data = payload.get("data") or {}
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

            items = customer_gets.get("items") or {}
            entitled_products: list[str] = []
            entitled_collections: list[str] = []
            # AllDiscountItems -> applies to all products; leave entitlements empty to signal global.
            products_block = items.get("products") if isinstance(items, dict) else None
            if isinstance(products_block, dict):
                p_nodes = (products_block.get("nodes") or []) if isinstance(products_block.get("nodes"), list) else products_block.get("nodes") or []
                for pn in p_nodes:
                    if pn and str(pn).strip():
                        entitled_products.append(str(pn).strip())
            collections_block = items.get("collections") if isinstance(items, dict) else None
            if isinstance(collections_block, dict):
                c_nodes = (collections_block.get("nodes") or []) if isinstance(collections_block.get("nodes"), list) else collections_block.get("nodes") or []
                for cn in c_nodes:
                    if cn and str(cn).strip():
                        entitled_collections.append(str(cn).strip())

            # BXGY trigger side: what the customer must buy (for upsell targeting).
            trigger_products: list[str] = []
            trigger_collections: list[str] = []
            customer_buys = disc.get("customerBuys") or {}
            buys_items = customer_buys.get("items") or {}
            buys_products_block = buys_items.get("products") if isinstance(buys_items, dict) else None
            if isinstance(buys_products_block, dict):
                bp_nodes = buys_products_block.get("nodes") or []
                for pn in bp_nodes:
                    if pn and str(pn).strip():
                        trigger_products.append(str(pn).strip())
            buys_collections_block = buys_items.get("collections") if isinstance(buys_items, dict) else None
            if isinstance(buys_collections_block, dict):
                bc_nodes = buys_collections_block.get("nodes") or []
                for cn in bc_nodes:
                    if cn and str(cn).strip():
                        trigger_collections.append(str(cn).strip())

            for code in codes:
                out.append(
                    {
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
                )
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


def get_order_status(shop_domain: str, access_token: str, order_id: str) -> dict[str, Any]:
    print(f"Getting order status for {order_id} from {shop_domain}")
    print(f"Access token: {access_token}")
    """
    Fetch order status from Shopify by order name/number (e.g. "#1001" or "1001").
    Uses the store's session. Returns a dict with order details or error.
    Sync; call via asyncio.to_thread from async code.
    """
    if not (order_id or "").strip():
        return {"found": False, "message": "Order ID is required", "prompting": True}
    order_id = (order_id or "").strip()
    if not order_id.startswith("#"):
        order_id = "#" + order_id
    shop = shop_domain.strip()
    if not shop.endswith(".myshopify.com"):
        shop = f"{shop.replace('https://', '').split('.')[0]}.myshopify.com"
    version = settings.shopify_api_version or "2024-01"
    try:
        with shopify.Session.temp(shop, version, access_token or ""):
            # List recent orders and find by name (API doesn't filter by name server-side)
            orders = shopify.Order.find(name=order_id, status="any")
            if not orders:
                return {"found": False, "message": "Order not found", "order_name": order_id}
            # Handle both list and single result
            order_list = orders if isinstance(orders, list) else list(orders)
            for order in order_list:
                name = getattr(order, "name", None) or ""
                if str(name).strip().lower() == order_id.lower():
                    return {
                        "found": True,
                        "order_name": name,
                        "id": getattr(order, "id", None),
                        "financial_status": getattr(order, "financial_status", None),
                        "fulfillment_status": getattr(order, "fulfillment_status", None) or "unfulfilled",
                        "total_price": getattr(order, "total_price", None),
                        "created_at": str(getattr(order, "created_at", "")),
                        "updated_at": str(getattr(order, "updated_at", "")),
                    }
            return {"found": False, "message": "Order not found", "order_name": order_id}
    except Exception as e:
        return {"found": False, "message": str(e), "order_name": order_id}


def get_orders_by_customer_email(
    shop_domain: str,
    access_token: str,
    customer_email: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Fetch recent orders for a customer by email. Returns list of dicts with order_name and line_items_summary.
    Sync; call via asyncio.to_thread from async code.
    """
    if not (customer_email or "").strip():
        return []
    email = (customer_email or "").strip().lower()
    shop = (shop_domain or "").strip()
    if not shop.endswith(".myshopify.com"):
        shop = f"{shop.replace('https://', '').split('.')[0]}.myshopify.com"
    version = settings.shopify_api_version or "2024-01"
    try:
        with shopify.Session.temp(shop, version, access_token or ""):
            # Fetch recent orders; API may not support filter by email, so we fetch and filter
            orders = shopify.Order.find(limit=min(limit * 2, 100), status="any", order="created_at DESC")
            order_list = orders if isinstance(orders, list) else list(orders)
            result = []
            for order in order_list:
                if len(result) >= limit:
                    break
                order_email = (getattr(order, "email", None) or "").strip().lower()
                if order_email != email:
                    continue
                order_name = getattr(order, "name", None) or ""
                line_items = getattr(order, "line_items", None) or []
                line_list = line_items if isinstance(line_items, list) else list(line_items)
                titles = [getattr(li, "title", "") or "" for li in line_list[:10]]
                summary = ", ".join(titles) if titles else "—"
                result.append({"order_name": str(order_name), "line_items_summary": summary})
            return result
    except Exception as e:
        print(f"get_orders_by_customer_email error: {e}")
        return []

