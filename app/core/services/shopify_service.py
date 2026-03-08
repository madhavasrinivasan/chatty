from __future__ import annotations

import base64
import binascii
import hashlib
import os
from typing import Any, List

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
        "write_products",
        "read_content",
        "read_locations",
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

