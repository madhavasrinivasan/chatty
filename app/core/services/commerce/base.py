"""
Commerce platform adapters — shared interface for Shopify vs Comez.

Capability map mirrors the Comez User API audit (`/user/home/*`):
  1. Customer by email/phone + orders
  2. Orders by email
  3. Order lookup by order number
  4. Live variant stock (partial on Comez)
  5. Active discounts / coupons (+ validate/apply)
  6. Returns / refunds (partial; JWT where required)
  7. Collections / categories
  8. Pages / CMS / policies
  9. Product by id / handle
  +. Guest cart by token
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CommerceAdapter(ABC):
    """Platform-agnostic commerce operations used by the chatbot."""

    # ── 1. Customer by email / phone → customer + recent orders ───────────

    @abstractmethod
    async def get_customer_by_contact(
        self, *, email: str | None = None, phone: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """
        Lookup customer + recent orders by email and/or phone.
        Shape: { customer, orders, orders_count }
        """
        ...

    # ── 2. Orders by customer email (last N) ──────────────────────────────

    @abstractmethod
    async def get_orders_by_email(self, email: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Last N orders for an email.
        Each item: { order_name, line_items_summary, total?, date?, payment_status?, raw? }
        """
        ...

    # ── 3. Order lookup by order number ───────────────────────────────────

    @abstractmethod
    def normalize_order_number(self, raw: str) -> str:
        """Strip shopper prefixes (#, ORD-, …) before lookup."""
        ...

    @abstractmethod
    async def get_order_status(self, order_number: str) -> dict[str, Any]:
        """
        Order support payload (found, order_name, fulfillment_status, financial_status,
        line_items, shipping_payload, …).
        """
        ...

    # ── 4. Live variant stock ─────────────────────────────────────────────

    @abstractmethod
    async def check_variant_stock(
        self, *, variant_id: str | int, quantity: int = 1
    ) -> dict[str, Any]:
        """
        Gate whether quantity is available for a variant.
        Shape: { available: bool, variant_id, quantity, message?, stock_quantity? }
        """
        ...

    async def get_variant_stock(
        self, *, variant_id: str | int | None = None, sku: str | None = None
    ) -> dict[str, Any]:
        """
        Optional richer stock lookup. Default: not supported / empty.
        Preferred shape: { found, available, stock_quantity, sku, product_id, variant_id }
        """
        return {
            "found": False,
            "available": False,
            "stock_quantity": None,
            "sku": sku,
            "product_id": None,
            "variant_id": variant_id,
            "message": "Stock-by-SKU/id not implemented for this platform",
        }

    # ── 5. Discounts / coupons ────────────────────────────────────────────

    @abstractmethod
    async def get_active_discounts(self) -> list[dict[str, Any]]:
        """Active public coupons / discount codes for pitching in chat."""
        ...

    async def validate_coupon(
        self,
        coupon: str,
        *,
        email: str | None = None,
        cart_token: str | None = None,
    ) -> dict[str, Any]:
        """Eligibility check. Default unsupported."""
        return {
            "coupon": coupon,
            "eligible": False,
            "message": "Coupon validation not implemented for this platform",
            "discount_id": None,
        }

    async def apply_coupon(
        self, coupon: str, *, cart_token: str | None = None
    ) -> dict[str, Any]:
        """Apply code to cart. Default unsupported."""
        return {
            "success": False,
            "coupon": coupon,
            "message": "Coupon apply not implemented for this platform",
            "cart": None,
        }

    # ── 6. Returns / refunds ──────────────────────────────────────────────

    async def get_returnable_items(self, order_number: str) -> list[dict[str, Any]]:
        """Line items eligible for return. Often missing on Comez today."""
        return []

    async def get_returns_by_user(self, user_id: str | int) -> list[dict[str, Any]]:
        """Refund/return rows for a customer (may require customer JWT)."""
        return []

    async def get_returns_by_order(self, order_number: str) -> list[dict[str, Any]]:
        """Refund/return status for an order number (may be broken on Comez)."""
        return []

    async def create_return_request(
        self,
        *,
        order_id: str,
        variant_id: str | int,
        description: str,
        return_type: str = "return",
        customer_jwt: str | None = None,
    ) -> dict[str, Any]:
        """Create a return/replace request (Comez needs customer JWT)."""
        return {
            "success": False,
            "message": "Return create not implemented for this platform",
        }

    # ── 7. Collections / categories ───────────────────────────────────────

    async def get_products_in_collection(
        self,
        *,
        slug: str | None = None,
        collection_id: str | int | None = None,
        page: int = 1,
        limit: int = 12,
        out_of_stock: str = "no",
    ) -> dict[str, Any]:
        """Products in a collection by slug or id."""
        return {
            "products": [],
            "pagination": {"page": page, "totalProducts": 0, "totalPages": 0},
            "collectionId": collection_id,
            "collectionName": None,
            "collectionSlug": slug,
        }

    async def get_main_categories(self) -> list[dict[str, Any]]:
        """Top-level categories."""
        return []

    async def get_products_by_category(
        self,
        *,
        slug: str | None = None,
        category_id: str | int | None = None,
        page: int = 1,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Products by category slug/id."""
        return {
            "products": [],
            "pagination": {"page": page, "totalProducts": 0, "totalPages": 0},
        }

    # ── 8. Pages / CMS / policies ─────────────────────────────────────────

    async def get_page(self, path: str) -> dict[str, Any]:
        """CMS page by path (e.g. /pages/faq). Shape: { path, html?, sections?, page? }."""
        return {"path": path, "page": None, "html": None, "sections": []}

    async def get_policy(self, kind: str) -> dict[str, Any]:
        """
        Policy HTML by kind: return | refund | shipping | terms | disclaimer | about.
        Shape: { kind, html }
        """
        return {"kind": kind, "html": None}

    # ── 9. Product by id / handle ─────────────────────────────────────────

    @abstractmethod
    async def get_product_by_handle(self, handle: str) -> dict[str, Any]:
        """
        Product detail by handle/slug.
        Shape: { found, handle, product_id, variants, attributes, raw }
        """
        ...

    @abstractmethod
    async def get_product_by_id(self, product_id: str | int) -> dict[str, Any]:
        """Same shape as get_product_by_handle, keyed by product id."""
        ...

    async def search_products(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        """Lightweight name search. Default empty."""
        return []

    # ── +. Guest cart ─────────────────────────────────────────────────────

    async def get_cart_by_token(self, cart_token: str) -> dict[str, Any]:
        """Guest cart lines. Shape: { items, cart_total, cart_token, raw }."""
        return {"items": [], "cart_total": None, "cart_token": cart_token or None, "raw": None}


def get_commerce_adapter(store) -> CommerceAdapter:
    """
    Factory: pick adapter from ecom_store.store_type.
    `store` is an ecom_store Tortoise model (or duck-typed object with needed fields).
    """
    store_type = getattr(store, "store_type", None) or "shopify"
    if hasattr(store_type, "value"):
        store_type = store_type.value
    store_type = str(store_type).lower().strip()

    if store_type == "comez":
        from app.core.services.commerce.comez_adapter import ComezCommerceAdapter

        return ComezCommerceAdapter(store)
    from app.core.services.commerce.shopify_adapter import ShopifyCommerceAdapter

    return ShopifyCommerceAdapter(store)
