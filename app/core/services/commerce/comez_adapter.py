from __future__ import annotations

import re
from typing import Any

from app.core.services.commerce.base import CommerceAdapter
from app.core.services.comez_service import ComezService


class ComezCommerceAdapter(CommerceAdapter):
    """Comez User API (`/user/home/*`) implementation of CommerceAdapter."""

    def __init__(self, store):
        self.store = store
        self.store_name = getattr(store, "store_name", "") or ""
        self.token = getattr(store, "access_token", "") or ""
        self.custom_domain = bool(getattr(store, "custom_domain", False))
        self.x_store = getattr(store, "x_store", None) or self.store_name
        self.storefront_url = (getattr(store, "storefront_url", None) or "").rstrip("/")

    def _kw(self) -> dict[str, Any]:
        return {
            "access_token": self.token,
            "custom_domain": self.custom_domain,
            "x_store": self.x_store,
        }

    def normalize_order_number(self, raw: str) -> str:
        s = (raw or "").strip()
        s = re.sub(r"^#\s*", "", s)
        s = re.sub(r"^ORD[-\s]*", "", s, flags=re.IGNORECASE)
        return s.strip()

    async def get_order_status(self, order_number: str) -> dict[str, Any]:
        return await ComezService.get_order_status(
            store_name=self.store_name,
            access_token=self.token,
            order_id=self.normalize_order_number(order_number),
            custom_domain=self.custom_domain,
            x_store=self.x_store,
        )

    async def get_orders_by_email(self, email: str, limit: int = 10) -> list[dict[str, Any]]:
        # → POST /user/home/getordersbyemail
        return await ComezService.get_orders_by_email(
            store_name=self.store_name,
            email=email,
            limit=limit,
            **self._kw(),
        )

    async def get_customer_by_contact(
        self, *, email: str | None = None, phone: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        # → POST /user/home/getcustomerbycontact
        return await ComezService.get_customer_by_contact(
            store_name=self.store_name,
            email=email,
            phone=phone,
            limit=limit,
            **self._kw(),
        )

    async def check_variant_stock(
        self, *, variant_id: str | int, quantity: int = 1
    ) -> dict[str, Any]:
        # → POST /user/home/check-product
        return await ComezService.check_product_stock(
            self.store_name,
            variant_id=variant_id,
            quantity=quantity,
            **self._kw(),
        )

    async def get_variant_stock(
        self, *, variant_id: str | int | None = None, sku: str | None = None
    ) -> dict[str, Any]:
        # No stock-by-SKU User API — needs product detail workaround when possible
        return await ComezService.get_variant_stock_from_product(
            self.store_name,
            variant_id=variant_id,
            sku=sku,
            **self._kw(),
        )

    async def get_active_discounts(self) -> list[dict[str, Any]]:
        # → POST /user/home/getactivecoupons
        return await ComezService.get_active_coupons(store_name=self.store_name, **self._kw())

    async def validate_coupon(
        self,
        coupon: str,
        *,
        email: str | None = None,
        cart_token: str | None = None,
    ) -> dict[str, Any]:
        return await ComezService.validate_coupon(
            self.store_name,
            coupon,
            email=email,
            cart_token=cart_token,
            **self._kw(),
        )

    async def apply_coupon(
        self, coupon: str, *, cart_token: str | None = None
    ) -> dict[str, Any]:
        return await ComezService.apply_coupon(
            self.store_name,
            coupon,
            cart_token=cart_token,
            **self._kw(),
        )

    async def get_returns_by_user(self, user_id: str | int) -> list[dict[str, Any]]:
        return await ComezService.get_returns_by_user(
            self.store_name, user_id, **self._kw()
        )

    async def create_return_request(
        self,
        *,
        order_id: str,
        variant_id: str | int,
        description: str,
        return_type: str = "return",
        customer_jwt: str | None = None,
    ) -> dict[str, Any]:
        return await ComezService.create_return(
            self.store_name,
            order_id=order_id,
            variant_id=variant_id,
            description=description,
            return_type=return_type,
            customer_jwt=customer_jwt,
            **self._kw(),
        )

    async def get_products_in_collection(
        self,
        *,
        slug: str | None = None,
        collection_id: str | int | None = None,
        page: int = 1,
        limit: int = 12,
        out_of_stock: str = "no",
    ) -> dict[str, Any]:
        return await ComezService.get_products_by_collection(
            self.store_name,
            slug=slug,
            collection_id=collection_id,
            page=page,
            limit=limit,
            out_of_stock=out_of_stock,
            **self._kw(),
        )

    async def get_main_categories(self) -> list[dict[str, Any]]:
        return await ComezService.get_main_categories(self.store_name, **self._kw())

    async def get_products_by_category(
        self,
        *,
        slug: str | None = None,
        category_id: str | int | None = None,
        page: int = 1,
        limit: int = 12,
    ) -> dict[str, Any]:
        return await ComezService.get_products_by_category(
            self.store_name,
            slug=slug,
            category_id=category_id,
            page=page,
            limit=limit,
            **self._kw(),
        )

    async def get_page(self, path: str) -> dict[str, Any]:
        return await ComezService.get_page(self.store_name, path, **self._kw())

    async def get_policy(self, kind: str) -> dict[str, Any]:
        return await ComezService.get_policy(self.store_name, kind, **self._kw())

    async def get_product_by_handle(self, handle: str) -> dict[str, Any]:
        # → POST /user/home/showdetails
        return await ComezService.get_product_by_slug(
            self.store_name, handle, **self._kw()
        )

    async def get_product_by_id(self, product_id: str | int) -> dict[str, Any]:
        # → POST /user/home/showdetailsbyproductid
        return await ComezService.get_product_by_id(
            self.store_name, product_id, **self._kw()
        )

    async def search_products(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        return await ComezService.search_products(
            self.store_name, query, limit=limit, **self._kw()
        )

    async def get_cart_by_token(self, cart_token: str) -> dict[str, Any]:
        return await ComezService.get_cart_by_token(
            store_name=self.store_name,
            cart_token=cart_token,
            **self._kw(),
        )
