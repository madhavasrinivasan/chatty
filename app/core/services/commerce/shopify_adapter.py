from __future__ import annotations

import asyncio
from typing import Any

from app.core.services.commerce.base import CommerceAdapter
from app.core.services.shopify_service import (
    get_active_discounts as shopify_get_active_discounts,
    get_order_status as shopify_get_order_status,
    get_orders_by_customer_email as shopify_get_orders_by_email,
)


class ShopifyCommerceAdapter(CommerceAdapter):
    """Shopify Admin API implementation of CommerceAdapter."""

    def __init__(self, store):
        self.store = store
        self.shop = getattr(store, "store_name", "") or ""
        self.token = getattr(store, "access_token", "") or ""

    def normalize_order_number(self, raw: str) -> str:
        return (raw or "").strip()

    async def get_order_status(self, order_number: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            shopify_get_order_status,
            self.shop,
            self.token,
            self.normalize_order_number(order_number),
        )

    async def get_orders_by_email(self, email: str, limit: int = 10) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            shopify_get_orders_by_email,
            self.shop,
            self.token,
            email,
            limit,
        )

    async def get_customer_by_contact(
        self, *, email: str | None = None, phone: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        orders = []
        if email:
            orders = await self.get_orders_by_email(email, limit=limit)
        return {
            "customer": {"email": email, "phone": phone} if (email or phone) else None,
            "orders": orders,
            "orders_count": len(orders),
        }

    async def check_variant_stock(
        self, *, variant_id: str | int, quantity: int = 1
    ) -> dict[str, Any]:
        # Inventory live-check lives in response_synthesis for product cards today.
        return {
            "available": True,
            "variant_id": variant_id,
            "quantity": quantity,
            "message": "Shopify stock gate not wired on adapter; treat as unknown/available",
            "stock_quantity": None,
        }

    async def get_active_discounts(self) -> list[dict[str, Any]]:
        return await shopify_get_active_discounts(self.shop, self.token)

    async def get_product_by_handle(self, handle: str) -> dict[str, Any]:
        return {
            "found": False,
            "handle": handle,
            "product_id": None,
            "variants": [],
            "attributes": [],
            "raw": None,
            "message": "Use Symma catalog / Shopify Admin product APIs separately",
        }

    async def get_product_by_id(self, product_id: str | int) -> dict[str, Any]:
        return {
            "found": False,
            "handle": None,
            "product_id": product_id,
            "variants": [],
            "attributes": [],
            "raw": None,
            "message": "Use Symma catalog / Shopify Admin product APIs separately",
        }
