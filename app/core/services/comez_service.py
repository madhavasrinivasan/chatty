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
    def build_headers(
        store_name: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        include_auth: bool = True,
    ) -> dict[str, str]:
        """Standard Comez tenant + auth headers for User and Editor APIs."""
        slug = (store_name or "").strip()
        x_store_val = (x_store or slug).strip()
        headers = {
            "storename": slug,
            "x-store": x_store_val,
            "x-custom-domain": "true" if custom_domain else "false",
            "Content-Type": "application/json",
        }
        if include_auth and access_token:
            token = access_token.strip()
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        return headers

    @staticmethod
    def _user_base() -> str:
        return f"{settings.comez_base_url.rstrip('/')}/user/home"

    @staticmethod
    async def fetch_all_products(
        store_name: str,
        timeout_s: float = 15.0,
        *,
        custom_domain: bool = False,
        x_store: str | None = None,
        access_token: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch all raw products from Comez backend.
        """
        url = f"{settings.comez_base_url.rstrip('/')}/editor/ecommerce/getallproducts"
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )

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
    def _parse_money(value: Any) -> float | None:
        """Parse Comez money fields; treat missing/blank as None (not 0)."""
        if value is None or value == "":
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _variant_unit_price(variant: dict[str, Any], product: dict[str, Any] | None = None) -> float | None:
        """
        Prefer effective_price, then v_price / price.
        Ignore special_price when it is 0 (Comez uses 0 for 'no sale').
        """
        product = product or {}
        for key in ("effective_price", "v_price", "price", "variant_price"):
            parsed = ComezService._parse_money(variant.get(key))
            if parsed is not None:
                return parsed
        special = ComezService._parse_money(variant.get("special_price"))
        if special is not None and special > 0:
            return special
        for key in ("price", "v_price", "effective_price"):
            parsed = ComezService._parse_money(product.get(key))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _first_image(raw_product: dict[str, Any]) -> str | None:
        raw_media = (
            raw_product.get("display_image")
            or raw_product.get("media")
            or raw_product.get("images")
            or None
        )
        if isinstance(raw_media, str) and (raw_media.startswith("[") or raw_media.startswith("{")):
            try:
                raw_media = json.loads(raw_media)
            except Exception:
                pass
        image_url = None
        if isinstance(raw_media, list) and raw_media:
            image_url = raw_media[0]
        elif isinstance(raw_media, dict):
            image_url = raw_media.get("src") or raw_media.get("url") or raw_media.get("path")
        elif isinstance(raw_media, str):
            image_url = raw_media
        if image_url and isinstance(image_url, str):
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                image_url = f"{settings.comez_base_url.rstrip('/')}/images/{image_url.lstrip('/')}"
            return image_url
        return None

    @staticmethod
    def transform_comez_product(raw_product: dict[str, Any]) -> dict[str, Any]:
        """
        Flatten Comez product JSON into store_knowledge database fields.

        Supports both editor `getallproducts` and storefront `viewallproducts` shapes.
        Product price = first variant's sellable price (effective / v_price).
        """
        pid = (
            raw_product.get("product_id")
            or raw_product.get("id")
            or raw_product.get("product_master_id")
            or ""
        )
        name = (
            raw_product.get("product_name")
            or raw_product.get("name")
            or ""
        )
        slug = raw_product.get("slug") or f"product-{pid}"
        desc = raw_product.get("description") or ""
        category = raw_product.get("category_name") or "General"
        vendor = (raw_product.get("vedor") or raw_product.get("vendor") or "").strip()
        seo_title = (raw_product.get("seo_title") or "").strip()
        seo_description = (raw_product.get("seo_description") or "").strip()

        soup = BeautifulSoup(desc or "", "html.parser")
        clean_desc = soup.get_text(separator=" ").strip()

        variants = raw_product.get("variants") or []
        if not isinstance(variants, list):
            variants = []

        total_stock = 0
        skus: list[str] = []
        barcodes: list[str] = []
        option_values: list[str] = []
        mapped_variants: list[dict[str, Any]] = []
        first_variant_price: float | None = None

        for idx, v in enumerate(variants):
            if not isinstance(v, dict):
                continue
            if str(v.get("status") or "active").lower() == "inactive":
                continue

            unit_price = ComezService._variant_unit_price(v, raw_product)
            if first_variant_price is None and unit_price is not None:
                first_variant_price = unit_price

            qty_raw = (
                v.get("stock_quantity")
                if v.get("stock_quantity") is not None
                else v.get("variant_quantity")
                if v.get("variant_quantity") is not None
                else v.get("inventory_quantity")
                if v.get("inventory_quantity") is not None
                else 0
            )
            try:
                qty = int(float(qty_raw))
            except (TypeError, ValueError):
                qty = 0
            total_stock += qty

            sku = v.get("v_sku") or v.get("sku")
            if sku and str(sku).strip() and str(sku).strip() not in skus:
                skus.append(str(sku).strip())

            barcode = v.get("barcode")
            if barcode and str(barcode).strip() and str(barcode).strip() not in barcodes:
                barcodes.append(str(barcode).strip())

            title = v.get("variant_name") or v.get("title") or "Default"
            if title and title != "Default" and str(title) not in option_values:
                option_values.append(str(title))

            mapped_variants.append({
                "id": str(v.get("id") or v.get("variant_id") or ""),
                "title": title,
                "sku": (str(sku).strip() if sku else ""),
                "price": float(unit_price) if unit_price is not None else 0.0,
                "inventory_quantity": qty,
                "barcode": str(barcode).strip() if barcode else "",
            })

        if first_variant_price is None:
            first_variant_price = ComezService._parse_money(raw_product.get("price")) or 0.0

        options_text = ", ".join(option_values)
        skus_text = ", ".join(skus) if skus else (raw_product.get("sku") or "")
        barcodes_text = ", ".join(barcodes)

        # Rich content blob → drives generated content_tsv (parity with Shopify transform)
        parts: list[str] = [str(name)]
        if clean_desc:
            parts.append(clean_desc)
        if vendor:
            parts.append(f"Vendor: {vendor}.")
        if category:
            parts.append(f"Category: {category}.")
            parts.append(f"Collections: {category}.")
        if options_text:
            parts.append(f"Available Options: {options_text}.")
        if skus_text:
            parts.append(f"SKUs: {skus_text}.")
        if barcodes_text:
            parts.append(f"Barcodes: {barcodes_text}.")
        parts.append(f"Price: {first_variant_price}.")
        if total_stock:
            parts.append(f"Stock: {total_stock}.")
        if seo_title:
            parts.append(f"SEO Title: {seo_title}.")
        if seo_description:
            parts.append(f"SEO Description: {seo_description}.")
        if category:
            parts.append(f"Tags: {category}.")
        content_blob = " ".join(parts).strip()

        content_hash = hashlib.md5(content_blob.encode("utf-8")).hexdigest()
        image_url = ComezService._first_image(raw_product)

        return {
            "shopify_product_id": f"comez_{pid}",
            "handle": slug,
            "title": name,
            "content": content_blob,
            "price": float(first_variant_price),
            "stock": total_stock,
            "image_url": image_url,
            "variant_data": mapped_variants,
            "content_hash": content_hash,
            "product_type": product_type.comez,
            "data_type": "product",
        }


    @staticmethod
    async def get_order_status(
        store_name: str,
        access_token: str,
        order_id: str,
        timeout_s: float = 15.0,
        *,
        custom_domain: bool = False,
        x_store: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch order status from Comez User API (preferred: getordersummary by display id).
        Falls back to getorderdetails. Returns a dict matching the Shopify status shape.
        """
        clean_id = re.sub(r"^#\s*", "", (order_id or "").strip())
        clean_id = re.sub(r"^ORD[-\s]*", "", clean_id, flags=re.IGNORECASE).strip()
        if not clean_id:
            return {"found": False, "message": "Missing order id", "order_name": order_id}

        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        base = ComezService._user_base()

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                # Prefer summary by display / gateway order id
                summary_resp = await client.post(
                    f"{base}/getordersummary",
                    json={"order_id": clean_id},
                    headers=headers,
                )
                if summary_resp.status_code in (200, 201):
                    data = (summary_resp.json() or {}).get("data") or {}
                    shipping = data.get("shipping") or {}
                    cart = data.get("cart") or {}
                    details = shipping.get("orderDetails") or {}
                    cart_items = cart.get("cartItems") or []
                    line_items = []
                    for row in cart_items:
                        if not isinstance(row, dict):
                            continue
                        title = row.get("product_name") or "Product"
                        variant = row.get("varaint_name") or row.get("variant_name") or ""
                        qty = int(row.get("quantity") or 0)
                        line = f"{title} ({variant})" if variant else title
                        line_items.append({
                            "title": title,
                            "quantity": qty,
                            "variant_title": variant,
                            "display_line": f"{line} × {qty}" if qty else line,
                        })
                    order_number = details.get("orderNumber") or shipping.get("orderId") or clean_id
                    return {
                        "found": True,
                        "order_name": f"#{order_number}" if not str(order_number).startswith("#") else str(order_number),
                        "fulfillment_status": None,
                        "financial_status": "paid",
                        "created_at_relative": "recently",
                        "line_items": line_items,
                        "shipping_payload": {
                            "fulfillments": [],
                            "summary_lines": [
                                f"Order {order_number} · total {cart.get('cartTotal')}",
                            ],
                        },
                        "customer_email": details.get("email"),
                        "raw_source": "getordersummary",
                    }

                # Fallback: line-item details
                detail_resp = await client.post(
                    f"{base}/getorderdetails",
                    json={"id": clean_id},
                    headers=headers,
                )
                if detail_resp.status_code not in (200, 201):
                    # Legacy editor endpoint
                    editor_url = f"{settings.comez_base_url.rstrip('/')}/editor/ecommerce/getorderdetails"
                    try:
                        numeric_id = int(clean_id)
                    except ValueError:
                        return {
                            "found": False,
                            "message": "Order not found",
                            "order_name": order_id,
                        }
                    detail_resp = await client.post(
                        editor_url,
                        json={"id": numeric_id},
                        headers=headers,
                    )

                if detail_resp.status_code not in (200, 201):
                    return {
                        "found": False,
                        "message": "Order not found or access unauthorized",
                        "order_name": order_id,
                    }

                data = (detail_resp.json() or {}).get("data") or {}
                orders = data.get("orders") or []
                if not orders:
                    return {"found": False, "message": "Order not found", "order_name": order_id}

                main_order = orders[0]
                line_items = []
                for row in orders:
                    qty = int(row.get("quantity") or 0)
                    item_name = row.get("product_name") or row.get("name") or "Product"
                    variant_name = row.get("variant_name") or ""
                    line = f"{item_name} ({variant_name})" if variant_name else item_name
                    line_items.append({
                        "title": item_name,
                        "quantity": qty,
                        "variant_title": variant_name,
                        "display_line": f"{line} × {qty}" if qty else line,
                    })

                delivery_status = main_order.get("delivery_status") or "pending"
                payment_status = (
                    main_order.get("payment_status")
                    or main_order.get("payment_satus")
                    or "unpaid"
                )
                tracking_id = main_order.get("tracking_id")
                courier = main_order.get("courier_company_name")
                summary_lines = []
                if tracking_id:
                    courier_part = f" · {courier}" if courier else ""
                    summary_lines.append(
                        f"Shipping status: {str(delivery_status).capitalize()}{courier_part} · Tracking: {tracking_id}"
                    )

                return {
                    "found": True,
                    "order_name": f"#{clean_id}",
                    "fulfillment_status": (
                        "fulfilled"
                        if delivery_status == "delivered"
                        else "partial"
                        if delivery_status == "shipped"
                        else None
                    ),
                    "financial_status": (
                        "paid"
                        if str(payment_status).lower() in ["paid", "success", "completed"]
                        else "unpaid"
                    ),
                    "created_at_relative": "recently",
                    "line_items": line_items,
                    "shipping_payload": {
                        "fulfillments": [
                            {
                                "status": delivery_status,
                                "tracking_company": courier,
                                "tracking_number": tracking_id,
                                "summary_line": summary_lines[0]
                                if summary_lines
                                else f"Status: {delivery_status}",
                            }
                        ]
                        if tracking_id or delivery_status
                        else [],
                        "summary_lines": summary_lines,
                    },
                }

        except Exception as e:
            print(f"⚠️ Error fetching order status from Comez: {e}")
            return {
                "found": False,
                "message": f"System error checking order status: {str(e)}",
                "order_name": order_id,
            }

    @staticmethod
    async def get_orders_by_email(
        store_name: str,
        email: str,
        *,
        limit: int = 10,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Return Shopify-shaped order summaries for prompt context."""
        email = (email or "").strip()
        if not email:
            return []
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/getordersbyemail"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    url,
                    json={"email": email, "limit": max(1, min(int(limit or 10), 50))},
                    headers=headers,
                )
            if resp.status_code not in (200, 201):
                print(f"⚠️ Comez get_orders_by_email failed: {resp.status_code} {resp.text[:200]}")
                return []
            data = (resp.json() or {}).get("data") or {}
            orders = data.get("orders") or []
            out = []
            for o in orders:
                if not isinstance(o, dict):
                    continue
                oid = o.get("id") or o.get("order_id") or ""
                out.append({
                    "order_name": f"#{oid}" if oid and not str(oid).startswith("#") else str(oid),
                    "line_items_summary": f"total {o.get('total')}",
                    "total": o.get("total"),
                    "date": o.get("date"),
                    "payment_status": o.get("payment_satus") or o.get("payment_status"),
                    "raw": o,
                })
            return out
        except Exception as e:
            print(f"⚠️ Comez get_orders_by_email error: {e}")
            return []

    @staticmethod
    async def get_customer_by_contact(
        store_name: str,
        *,
        email: str | None = None,
        phone: str | None = None,
        limit: int = 10,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        email = (email or "").strip() or None
        phone = (phone or "").strip() or None
        if not email and not phone:
            return {"customer": None, "orders": [], "orders_count": 0}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        body: dict[str, Any] = {"limit": max(1, min(int(limit or 10), 50))}
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        url = f"{ComezService._user_base()}/getcustomerbycontact"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                print(f"⚠️ Comez get_customer_by_contact failed: {resp.status_code}")
                return {"customer": None, "orders": [], "orders_count": 0}
            data = (resp.json() or {}).get("data") or {}
            return {
                "customer": data.get("customer"),
                "orders": data.get("orders") or [],
                "orders_count": int(data.get("orders_count") or 0),
            }
        except Exception as e:
            print(f"⚠️ Comez get_customer_by_contact error: {e}")
            return {"customer": None, "orders": [], "orders_count": 0}

    @staticmethod
    async def get_active_coupons(
        store_name: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Map Comez public coupons into the discount_info-ish shape used in chat."""
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/getactivecoupons"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={}, headers=headers)
            if resp.status_code not in (200, 201):
                print(f"⚠️ Comez get_active_coupons failed: {resp.status_code}")
                return []
            data = (resp.json() or {}).get("data") or {}
            coupons = data.get("coupons") or []
            out = []
            for c in coupons:
                if not isinstance(c, dict):
                    continue
                reduction = (c.get("reduction_method") or "").lower()
                value = c.get("percentage") if reduction == "percentage" else c.get("amount")
                out.append({
                    "code": c.get("code"),
                    "title": c.get("title") or c.get("code"),
                    "type": reduction or c.get("discount_type"),
                    "value": value,
                    "currency": "INR",
                    "minimum_price": c.get("minimum_price"),
                    "description": c.get("description"),
                    "scope": c.get("scope") or {},
                    "raw": c,
                })
            return out
        except Exception as e:
            print(f"⚠️ Comez get_active_coupons error: {e}")
            return []

    @staticmethod
    def _normalize_cart_line(row: dict[str, Any]) -> dict[str, Any]:
        """Map a Comez Finalcart row into the Chatty cart_items shape (major currency units)."""
        qty = row.get("quantity")
        try:
            quantity = int(float(qty)) if qty is not None else 0
        except (TypeError, ValueError):
            quantity = 0

        unit: float | None = None
        for key in ("final_price", "special_price", "amount", "price", "v_price"):
            raw = row.get(key)
            if raw is None or raw == "":
                continue
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                continue
            # `amount` on Comez lines is often line total (unit * qty)
            if key == "amount" and quantity > 0:
                unit = parsed / quantity
            else:
                unit = parsed
            break
        if unit is None:
            unit = 0.0

        title = (
            row.get("product_name")
            or row.get("title")
            or row.get("name")
            or "Product"
        )
        variant = row.get("varaint_name") or row.get("variant_name") or ""
        if variant:
            title = f"{title} ({variant})"

        return {
            "id": row.get("variant_id") or row.get("id"),
            "product_id": row.get("product_id"),
            "title": title,
            "quantity": quantity,
            "price": float(unit),
        }

    @staticmethod
    async def get_cart_by_token(
        store_name: str,
        cart_token: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        """
        Fetch guest cart from Comez User API using cart_token.
        Returns { items: [...], cart_total: float, cart_token, raw }.
        """
        token = (cart_token or "").strip()
        if not token:
            return {"items": [], "cart_total": None, "cart_token": None, "raw": None}

        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/getcart"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    url,
                    json={"cartToken": token, "isGuest": True},
                    headers=headers,
                )
            if resp.status_code not in (200, 201):
                print(f"⚠️ Comez get_cart_by_token failed: {resp.status_code} {resp.text[:200]}")
                return {"items": [], "cart_total": None, "cart_token": token, "raw": None}

            envelope = resp.json() if resp.content else {}
            data = envelope.get("data") if isinstance(envelope, dict) else None
            if not isinstance(data, dict):
                return {"items": [], "cart_total": None, "cart_token": token, "raw": None}

            raw_items = data.get("Finalcart") or data.get("cartItems") or []
            items: list[dict[str, Any]] = []
            if isinstance(raw_items, list):
                for row in raw_items:
                    if isinstance(row, dict):
                        items.append(ComezService._normalize_cart_line(row))

            total_raw = data.get("total_amount")
            try:
                cart_total = float(total_raw) if total_raw is not None else None
            except (TypeError, ValueError):
                cart_total = None
            if cart_total is None and items:
                cart_total = sum(
                    float(i.get("price") or 0) * int(i.get("quantity") or 0) for i in items
                )

            return {
                "items": items,
                "cart_total": cart_total,
                "cart_token": data.get("cartToken") or token,
                "raw": data,
            }
        except Exception as e:
            print(f"⚠️ Comez get_cart_by_token error: {e}")
            return {"items": [], "cart_total": None, "cart_token": token, "raw": None}

    # ── Stock / product / collection / CMS (User API parity) ───────────────

    @staticmethod
    def _normalize_product_detail(data: dict[str, Any], *, handle: str | None = None) -> dict[str, Any]:
        variants_raw = data.get("variant") or data.get("variants") or []
        if not isinstance(variants_raw, list):
            variants_raw = []
        variants = []
        product_id = None
        resolved_handle = handle
        for v in variants_raw:
            if not isinstance(v, dict):
                continue
            pid = v.get("product-id") or v.get("product_id") or v.get("product_master_id")
            if product_id is None and pid is not None:
                product_id = pid
            if not resolved_handle:
                resolved_handle = v.get("slug")
            stock = v.get("stock_quantity")
            try:
                stock_n = int(stock) if stock is not None else None
            except (TypeError, ValueError):
                stock_n = None
            variants.append({
                "id": v.get("id"),
                "product_id": pid,
                "title": v.get("name") or v.get("product_name"),
                "variant_title": v.get("variant_name") or v.get("varaint_name"),
                "sku": v.get("v_sku") or v.get("sku"),
                "price": ComezService._parse_money(v.get("special_price"))
                if ComezService._parse_money(v.get("special_price")) and ComezService._parse_money(v.get("special_price")) > 0
                else ComezService._parse_money(v.get("v_price") or v.get("price")),
                "stock_quantity": stock_n,
                "available": (stock_n is None) or stock_n > 0,
                "images": v.get("images") or v.get("media") or [],
                "raw": v,
            })
        return {
            "found": bool(variants),
            "handle": resolved_handle,
            "product_id": product_id,
            "variants": variants,
            "attributes": data.get("attributes") or [],
            "active_discounts": data.get("activeiscounts") or data.get("active_discounts") or [],
            "raw": data,
        }

    @staticmethod
    async def check_product_stock(
        store_name: str,
        *,
        variant_id: str | int,
        quantity: int = 1,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        """POST /user/home/check-product — gate by variant_id + quantity (no qty returned)."""
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/check-product"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    url,
                    json={"variant_id": variant_id, "quantity": max(1, int(quantity or 1))},
                    headers=headers,
                )
            body = resp.json() if resp.content else {}
            if resp.status_code in (200, 201) and (body.get("status") in (200, 201, None) or body.get("data") is None):
                # Success envelope often has data: null
                if resp.status_code in (200, 201) and body.get("status", 201) not in (400, 404, 500):
                    if body.get("status") == 400 or (isinstance(body.get("message"), str) and "not available" in body["message"].lower()):
                        return {
                            "available": False,
                            "variant_id": variant_id,
                            "quantity": quantity,
                            "message": body.get("message") or "Stock not available",
                            "stock_quantity": None,
                        }
                    return {
                        "available": True,
                        "variant_id": variant_id,
                        "quantity": quantity,
                        "message": None,
                        "stock_quantity": None,
                    }
            msg = body.get("message") or f"Stock check failed ({resp.status_code})"
            return {
                "available": False,
                "variant_id": variant_id,
                "quantity": quantity,
                "message": msg,
                "stock_quantity": None,
            }
        except Exception as e:
            print(f"⚠️ Comez check_product_stock error: {e}")
            return {
                "available": False,
                "variant_id": variant_id,
                "quantity": quantity,
                "message": str(e),
                "stock_quantity": None,
            }

    @staticmethod
    async def get_product_by_slug(
        store_name: str,
        slug: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        slug = (slug or "").strip().lstrip("/")
        if not slug:
            return {"found": False, "handle": slug, "product_id": None, "variants": [], "attributes": [], "raw": None}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/showdetails"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={"slug": slug}, headers=headers)
            if resp.status_code not in (200, 201):
                return {"found": False, "handle": slug, "product_id": None, "variants": [], "attributes": [], "raw": None}
            data = (resp.json() or {}).get("data") or {}
            return ComezService._normalize_product_detail(data if isinstance(data, dict) else {}, handle=slug)
        except Exception as e:
            print(f"⚠️ Comez get_product_by_slug error: {e}")
            return {"found": False, "handle": slug, "product_id": None, "variants": [], "attributes": [], "raw": None}

    @staticmethod
    async def get_product_by_id(
        store_name: str,
        product_id: str | int,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        if product_id is None or product_id == "":
            return {"found": False, "handle": None, "product_id": product_id, "variants": [], "attributes": [], "raw": None}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/showdetailsbyproductid"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={"productid": product_id}, headers=headers)
            if resp.status_code not in (200, 201):
                return {"found": False, "handle": None, "product_id": product_id, "variants": [], "attributes": [], "raw": None}
            data = (resp.json() or {}).get("data") or {}
            return ComezService._normalize_product_detail(data if isinstance(data, dict) else {})
        except Exception as e:
            print(f"⚠️ Comez get_product_by_id error: {e}")
            return {"found": False, "handle": None, "product_id": product_id, "variants": [], "attributes": [], "raw": None}

    @staticmethod
    async def get_variant_stock_from_product(
        store_name: str,
        *,
        variant_id: str | int | None = None,
        sku: str | None = None,
        product_id: str | int | None = None,
        handle: str | None = None,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
    ) -> dict[str, Any]:
        """
        Workaround for missing stock-by-SKU endpoint: load product detail and match variant.
        Prefer product_id/handle when known; otherwise returns not found for SKU-only.
        """
        detail: dict[str, Any] = {"found": False, "variants": []}
        if product_id is not None and product_id != "":
            detail = await ComezService.get_product_by_id(
                store_name,
                product_id,
                access_token=access_token,
                custom_domain=custom_domain,
                x_store=x_store,
            )
        elif handle:
            detail = await ComezService.get_product_by_slug(
                store_name,
                handle,
                access_token=access_token,
                custom_domain=custom_domain,
                x_store=x_store,
            )
        else:
            return {
                "found": False,
                "available": False,
                "stock_quantity": None,
                "sku": sku,
                "product_id": None,
                "variant_id": variant_id,
                "message": "Comez has no stock-by-SKU API; pass product_id or handle",
            }

        want_vid = str(variant_id) if variant_id is not None else None
        want_sku = (sku or "").strip().lower() or None
        for v in detail.get("variants") or []:
            if want_vid and str(v.get("id")) == want_vid:
                qty = v.get("stock_quantity")
                return {
                    "found": True,
                    "available": bool(v.get("available")),
                    "stock_quantity": qty,
                    "sku": v.get("sku"),
                    "product_id": v.get("product_id") or detail.get("product_id"),
                    "variant_id": v.get("id"),
                    "message": None,
                }
            if want_sku and (v.get("sku") or "").strip().lower() == want_sku:
                qty = v.get("stock_quantity")
                return {
                    "found": True,
                    "available": bool(v.get("available")),
                    "stock_quantity": qty,
                    "sku": v.get("sku"),
                    "product_id": v.get("product_id") or detail.get("product_id"),
                    "variant_id": v.get("id"),
                    "message": None,
                }
        return {
            "found": False,
            "available": False,
            "stock_quantity": None,
            "sku": sku,
            "product_id": detail.get("product_id"),
            "variant_id": variant_id,
            "message": "Variant not found on product",
        }

    @staticmethod
    async def validate_coupon(
        store_name: str,
        coupon: str,
        *,
        email: str | None = None,
        cart_token: str | None = None,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        code = (coupon or "").strip()
        if not code:
            return {"coupon": coupon, "eligible": False, "message": "Missing coupon", "discount_id": None}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        body: dict[str, Any] = {"coupon": code}
        if email:
            body["email"] = email.strip()
        if cart_token:
            body["cartToken"] = cart_token.strip()
        url = f"{ComezService._user_base()}/validatecoupon"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
            data = (resp.json() or {}).get("data") if resp.content else None
            if not isinstance(data, dict):
                data = {}
            eligible = bool(data.get("eligible")) if resp.status_code in (200, 201) else False
            return {
                "coupon": data.get("coupon") or code,
                "eligible": eligible,
                "message": data.get("message"),
                "discount_id": data.get("discount_id"),
                "raw": data,
            }
        except Exception as e:
            print(f"⚠️ Comez validate_coupon error: {e}")
            return {"coupon": code, "eligible": False, "message": str(e), "discount_id": None}

    @staticmethod
    async def apply_coupon(
        store_name: str,
        coupon: str,
        *,
        cart_token: str | None = None,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        code = (coupon or "").strip()
        if not code:
            return {"success": False, "coupon": coupon, "message": "Missing coupon", "cart": None}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        body: dict[str, Any] = {"coupon": code}
        if cart_token:
            body["cartToken"] = cart_token.strip()
        url = f"{ComezService._user_base()}/getcoupon"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                msg = None
                try:
                    msg = (resp.json() or {}).get("message") or (resp.json() or {}).get("error", {}).get("message")
                except Exception:
                    msg = resp.text[:200]
                return {"success": False, "coupon": code, "message": msg or f"HTTP {resp.status_code}", "cart": None}
            data = (resp.json() or {}).get("data")
            return {"success": True, "coupon": code, "message": "applied", "cart": data}
        except Exception as e:
            print(f"⚠️ Comez apply_coupon error: {e}")
            return {"success": False, "coupon": code, "message": str(e), "cart": None}

    @staticmethod
    async def get_returns_by_user(
        store_name: str,
        user_id: str | int,
        *,
        customer_jwt: str | None = None,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        """POST /user/home/getreturnbyuser — requires customer JWT when available."""
        token = (customer_jwt or access_token or "").strip() or None
        headers = ComezService.build_headers(
            store_name,
            access_token=token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(token),
        )
        url = f"{ComezService._user_base()}/getreturnbyuser"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={"user_id": user_id}, headers=headers)
            if resp.status_code not in (200, 201):
                print(f"⚠️ Comez get_returns_by_user failed: {resp.status_code}")
                return []
            data = (resp.json() or {}).get("data")
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"⚠️ Comez get_returns_by_user error: {e}")
            return []

    @staticmethod
    async def create_return(
        store_name: str,
        *,
        order_id: str,
        variant_id: str | int,
        description: str,
        return_type: str = "return",
        customer_jwt: str | None = None,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 20.0,
    ) -> dict[str, Any]:
        """
        POST /user/home/createreturn — multipart; customer JWT required on Comez.
        Image upload omitted (chatbot text-only path).
        """
        token = (customer_jwt or "").strip()
        if not token:
            return {"success": False, "message": "Customer JWT required for Comez returns"}
        headers = ComezService.build_headers(
            store_name,
            access_token=token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=True,
        )
        # multipart: drop JSON content-type
        headers.pop("Content-Type", None)
        url = f"{ComezService._user_base()}/createreturn"
        files = {
            "order_id": (None, str(order_id)),
            "variant_id": (None, str(variant_id)),
            "type": (None, return_type or "return"),
            "description": (None, description or ""),
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, files=files, headers=headers)
            if resp.status_code not in (200, 201):
                try:
                    msg = (resp.json() or {}).get("message") or resp.text[:200]
                except Exception:
                    msg = resp.text[:200]
                return {"success": False, "message": msg}
            return {"success": True, "message": "success", "raw": (resp.json() or {}).get("data")}
        except Exception as e:
            print(f"⚠️ Comez create_return error: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    async def get_products_by_collection(
        store_name: str,
        *,
        slug: str | None = None,
        collection_id: str | int | None = None,
        page: int = 1,
        limit: int = 12,
        out_of_stock: str = "no",
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        empty = {
            "products": [],
            "pagination": {"page": page, "totalProducts": 0, "totalPages": 0},
            "collectionId": collection_id,
            "collectionName": None,
            "collectionSlug": slug,
        }
        if not slug and collection_id is None:
            return empty
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        body: dict[str, Any] = {
            "query": {
                "page": max(1, int(page or 1)),
                "limit": max(1, min(int(limit or 12), 50)),
                "sortBy": "Featured",
                "outofstock": out_of_stock or "no",
            }
        }
        if slug:
            body["slug"] = slug
        if collection_id is not None:
            body["id"] = collection_id
            body["collectionId"] = collection_id
        url = f"{ComezService._user_base()}/getproductsbycollection"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                return empty
            data = (resp.json() or {}).get("data") or {}
            if not isinstance(data, dict):
                return empty
            return {
                "products": data.get("products") or [],
                "pagination": data.get("pagination") or empty["pagination"],
                "collectionId": data.get("collectionId") or collection_id,
                "collectionName": data.get("collectionName"),
                "collectionSlug": data.get("collectionSlug") or slug,
                "filters": data.get("filters"),
            }
        except Exception as e:
            print(f"⚠️ Comez get_products_by_collection error: {e}")
            return empty

    @staticmethod
    async def get_main_categories(
        store_name: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/getmaincategory"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={}, headers=headers)
            if resp.status_code not in (200, 201):
                return []
            data = (resp.json() or {}).get("data")
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"⚠️ Comez get_main_categories error: {e}")
            return []

    @staticmethod
    async def get_products_by_category(
        store_name: str,
        *,
        slug: str | None = None,
        category_id: str | int | None = None,
        page: int = 1,
        limit: int = 12,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        empty = {"products": [], "pagination": {"page": page, "totalProducts": 0, "totalPages": 0}}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        body: dict[str, Any] = {
            "query": {"page": max(1, int(page or 1)), "limit": max(1, min(int(limit or 12), 50))},
        }
        if slug:
            body["slug"] = slug
        if category_id is not None:
            body["id"] = category_id
            body["categoryId"] = category_id
        url = f"{ComezService._user_base()}/getproductsbycategory"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                return empty
            data = (resp.json() or {}).get("data") or {}
            if not isinstance(data, dict):
                # some handlers return list directly
                if isinstance(data, list):
                    return {"products": data, "pagination": empty["pagination"]}
                return empty
            return {
                "products": data.get("products") or data.get("data") or [],
                "pagination": data.get("pagination") or empty["pagination"],
            }
        except Exception as e:
            print(f"⚠️ Comez get_products_by_category error: {e}")
            return empty

    @staticmethod
    async def get_page(
        store_name: str,
        path: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        path = (path or "").strip() or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/getpages"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={"path": path}, headers=headers)
            if resp.status_code not in (200, 201):
                return {"path": path, "page": None, "html": None, "sections": []}
            data = (resp.json() or {}).get("data") or {}
            page = data.get("page") if isinstance(data, dict) else None
            sections = []
            if isinstance(page, dict):
                sections = page.get("sections") or []
            return {
                "path": path,
                "page": page,
                "html": data.get("html") if isinstance(data, dict) else None,
                "sections": sections,
                "raw": data,
            }
        except Exception as e:
            print(f"⚠️ Comez get_page error: {e}")
            return {"path": path, "page": None, "html": None, "sections": []}

    _POLICY_ENDPOINTS = {
        "return": "getreturnpolicy",
        "refund": "getrefundpolicy",
        "shipping": "getshipingpolicy",
        "terms": "getterms",
        "disclaimer": "getdisclaimer",
        "about": "getactiveabout",
    }

    @staticmethod
    async def get_policy(
        store_name: str,
        kind: str,
        *,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        key = (kind or "").strip().lower()
        endpoint = ComezService._POLICY_ENDPOINTS.get(key)
        if not endpoint:
            return {"kind": kind, "html": None, "message": f"Unknown policy kind: {kind}"}
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        url = f"{ComezService._user_base()}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json={}, headers=headers)
            if resp.status_code not in (200, 201):
                return {"kind": kind, "html": None}
            data = (resp.json() or {}).get("data")
            if isinstance(data, str):
                return {"kind": kind, "html": data}
            return {"kind": kind, "html": None, "raw": data}
        except Exception as e:
            print(f"⚠️ Comez get_policy error: {e}")
            return {"kind": kind, "html": None}

    @staticmethod
    async def search_products(
        store_name: str,
        query: str,
        *,
        limit: int = 12,
        access_token: str | None = None,
        custom_domain: bool = False,
        x_store: str | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        headers = ComezService.build_headers(
            store_name,
            access_token=access_token,
            custom_domain=custom_domain,
            x_store=x_store,
            include_auth=bool(access_token),
        )
        # Audit: uses query string ?search=, not body
        url = f"{ComezService._user_base()}/serachproducts"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, params={"search": q}, json={}, headers=headers)
            if resp.status_code not in (200, 201):
                return []
            data = (resp.json() or {}).get("data")
            if isinstance(data, list):
                return data[: max(1, min(int(limit or 12), 50))]
            if isinstance(data, dict):
                products = data.get("products") or data.get("data") or []
                return products[: max(1, min(int(limit or 12), 50))] if isinstance(products, list) else []
            return []
        except Exception as e:
            print(f"⚠️ Comez search_products error: {e}")
            return []
