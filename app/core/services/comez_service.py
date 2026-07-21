from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.config.config import settings
from app.core.models.models import product_type


class ComezService:
    @staticmethod
    async def fetch_all_products(store_name: str, timeout_s: float = 15.0) -> list[dict[str, Any]]:
        """
        Fetch all raw products from Comez backend.
        """
        url = f"{settings.comez_base_url}/editor/ecommerce/getallproducts"
        headers = {
            "storename": store_name,
            "x-custom-domain": "false",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(url, headers=headers)
            
            if resp.status_code != 200:
                print(f"⚠️ Comez fetch_all_products failed with status {resp.status_code}: {resp.text[:200]}")
                return []
            
            payload = resp.json() if resp.content else {}
            raw_items = payload.get("data") or []
            if not isinstance(raw_items, list):
                print("⚠️ Comez fetch_all_products returned non-list data.")
                return []

            return raw_items

        except Exception as e:
            print(f"⚠️ Error fetching products from Comez: {e}")
            return []

    @staticmethod
    def transform_comez_product(raw_product: dict[str, Any]) -> dict[str, Any]:
        """
        Flatten Comez product JSON into store_knowledge database fields.
        """
        pid = raw_product.get("id") or ""
        name = raw_product.get("name") or ""
        slug = raw_product.get("slug") or f"product-{pid}"
        desc = raw_product.get("description") or ""
        category = raw_product.get("category_name") or "General"
        vendor = raw_product.get("vedor") or raw_product.get("vendor") or ""
        
        # Clean HTML description
        soup = BeautifulSoup(desc, "html.parser")
        clean_desc = soup.get_text(separator=" ").strip()

        variants = raw_product.get("variants") or []
        prices: list[float] = []
        total_stock = 0
        skus: list[str] = []
        option_values: list[str] = []

        mapped_variants = []
        for v in variants:
            if not isinstance(v, dict):
                continue
            
            # Extract price from variant, fallback to product level
            price_val = v.get("v_price") or v.get("price") or v.get("special_price") or raw_product.get("price")
            if price_val is not None:
                try:
                    prices.append(float(price_val))
                except (TypeError, ValueError):
                    pass

            qty = v.get("variant_quantity") or v.get("inventory_quantity") or 0
            if isinstance(qty, (int, float, str)):
                try:
                    total_stock += int(qty)
                except ValueError:
                    pass

            sku = v.get("v_sku") or v.get("sku")
            if sku and str(sku).strip():
                skus.append(str(sku).strip())

            title = v.get("variant_name") or v.get("title") or "Default"
            if title and title != "Default":
                option_values.append(str(title))

            mapped_variants.append({
                "title": title,
                "sku": sku or "",
                "price": price_val or 0.0,
                "inventory_quantity": qty
            })

        min_price = min(prices) if prices else float(raw_product.get("price") or 0.0)
        options_text = ", ".join(option_values)
        skus_text = ", ".join(skus) if skus else raw_product.get("sku") or ""

        # Build content blob for the embedding search model
        parts = [name]
        if clean_desc:
            parts.append(clean_desc)
        if vendor:
            parts.append(f"Vendor: {vendor}.")
        if category:
            parts.append(f"Category: {category}.")
        if options_text:
            parts.append(f"Available Options: {options_text}.")
        if skus_text:
            parts.append(f"SKUs: {skus_text}.")
        content_blob = " ".join(parts).strip()

        content_hash = hashlib.md5(content_blob.encode("utf-8")).hexdigest()

        # Parse media path (Comez media is typically path or URL, or list/JSON thereof)
        raw_media = raw_product.get("media") or None
        
        if isinstance(raw_media, str) and (raw_media.startswith("[") or raw_media.startswith("{")):
            try:
                raw_media = json.loads(raw_media)
            except Exception:
                pass

        image_url = None
        if isinstance(raw_media, list) and raw_media:
            image_url = raw_media[0]
        elif isinstance(raw_media, str):
            image_url = raw_media

        if image_url and isinstance(image_url, str):
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                image_url = f"{settings.comez_base_url}/images/{image_url.lstrip('/')}"

        # Return aligned store_knowledge fields
        return {
            "shopify_product_id": f"comez_{pid}",  # Prefix prevents unique constraint collision
            "handle": slug,
            "title": name,
            "content": content_blob,
            "price": min_price,
            "stock": total_stock,
            "image_url": image_url,
            "variant_data": mapped_variants,
            "content_hash": content_hash,
            "product_type": product_type.comez,
            "data_type": "product",
        }

    @staticmethod
    async def get_order_status(store_name: str, access_token: str, order_id: str, timeout_s: float = 15.0) -> dict[str, Any]:
        """
        Fetch order status and tracking details from Comez.
        Returns a dict matching the Shopify status shape expected by the orchestrator.
        """
        url = f"{settings.comez_base_url}/editor/ecommerce/getorderdetails"
        headers = {
            "storename": store_name,
            "x-custom-domain": "false",
            "Authorization": f"Bearer {access_token}" if access_token else "",
            "Content-Type": "application/json",
        }

        # Try to parse order_id as integer if numerical
        clean_id = order_id.replace("#", "").strip()
        try:
            numeric_id = int(clean_id)
        except ValueError:
            return {"found": False, "message": f"Invalid Order ID format: {order_id}", "order_name": order_id}

        payload = {"id": numeric_id}

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code != 200:
                print(f"⚠️ Comez get_order_status failed with status {resp.status_code}: {resp.text[:200]}")
                return {"found": False, "message": "Order not found or access unauthorized", "order_name": order_id}

            data = resp.json().get("data") or {}
            orders = data.get("orders") or []
            if not orders:
                return {"found": False, "message": "Order not found", "order_name": order_id}

            # Map the response. In Comez, orders returns joined rows for order items.
            main_order = orders[0]
            
            # Map order items
            line_items = []
            for row in orders:
                qty = int(row.get("quantity") or 0)
                item_name = row.get("name") or row.get("product_name") or "Product"
                variant_name = row.get("variant_name") or ""
                line = f"{item_name}"
                if variant_name:
                    line = f"{item_name} ({variant_name})"
                
                line_items.append({
                    "title": item_name,
                    "quantity": qty,
                    "variant_title": variant_name,
                    "display_line": f"{line} × {qty}" if qty else line,
                })

            delivery_status = main_order.get("delivery_status") or "pending"
            payment_status = main_order.get("payment_status") or main_order.get("payment_satus") or "unpaid"

            # Fulfillments mapping (mocking tracking summary lines)
            summary_lines = []
            tracking_id = main_order.get("tracking_id")
            courier = main_order.get("courier_company_name")
            if tracking_id:
                courier_part = f" · {courier}" if courier else ""
                summary_lines.append(f"Shipping status: {delivery_status.capitalize()}{courier_part} · Tracking: {tracking_id}")

            return {
                "found": True,
                "order_name": f"#{clean_id}",
                "fulfillment_status": "fulfilled" if delivery_status == "delivered" else "partial" if delivery_status == "shipped" else None,
                "financial_status": "paid" if payment_status.lower() in ["paid", "success", "completed"] else "unpaid",
                "created_at_relative": "recently",
                "line_items": line_items,
                "shipping_payload": {
                    "fulfillments": [
                        {
                            "status": delivery_status,
                            "tracking_company": courier,
                            "tracking_number": tracking_id,
                            "summary_line": summary_lines[0] if summary_lines else f"Status: {delivery_status}",
                        }
                    ] if tracking_id or delivery_status else [],
                    "summary_lines": summary_lines,
                }
            }

        except Exception as e:
            print(f"⚠️ Error fetching order status from Comez: {e}")
            return {"found": False, "message": f"System error checking order status: {str(e)}", "order_name": order_id}
