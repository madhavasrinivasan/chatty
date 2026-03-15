"""
Shopify returns via GraphQL Admin API: fetch return eligibility and (later) create return.
Uses httpx for async GraphQL requests.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config.config import settings

# API version 2024-04+ for order line items and returnable fulfillments
ORDER_LINE_ITEMS_API_VERSION = "2024-04"

# Query 1: Fetch order ID by name (order doesn't have returnableFulfillments; use top-level returnableFulfillments(orderId) instead)
GET_ORDER_ID_QUERY = """
query getOrderId($query: String!) {
  orders(first: 1, query: $query) {
    edges {
      node {
        id
      }
    }
  }
}
"""

# Query 2: Top-level returnableFulfillments scoped by orderId (required by Shopify).
# ReturnableFulfillmentLineItem has only quantity + fulfillmentLineItem; lineItem is on FulfillmentLineItem.
GET_RETURNABLE_FULFILLMENTS_BY_ORDER_QUERY = """
query getReturnableFulfillments($orderId: ID!) {
  returnableFulfillments(orderId: $orderId, first: 10) {
    edges {
      node {
        returnableFulfillmentLineItems(first: 10) {
          edges {
            node {
              quantity
              fulfillmentLineItem {
                id
                lineItem { id title }
              }
            }
          }
        }
      }
    }
  }
}
"""

GET_ORDER_LINE_ITEMS_UI_QUERY = """
query getOrderLineItemsForUI($query: String!) {
  orders(first: 1, query: $query) {
    edges {
      node {
        id
        lineItems(first: 50) {
          edges {
            node {
              id
              title
              quantity
              image { url }
            }
          }
        }
      }
    }
  }
}
"""


def _normalize_shop(shop_domain: str) -> str:
    shop = (shop_domain or "").strip()
    if not shop.endswith(".myshopify.com"):
        shop = f"{shop.replace('https://', '').split('.')[0]}.myshopify.com"
    return shop


class ShopifyReturnService:
    """Async service for Shopify returns using GraphQL Admin API."""

    def __init__(self, shop_domain: str, access_token: str):
        self.shop = _normalize_shop(shop_domain)
        self.access_token = (access_token or "").strip()
        self.version = getattr(settings, "shopify_api_version", None) or "2024-01"
        self.base_url = f"https://{self.shop}/admin/api/{self.version}/graphql.json"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": self.access_token,
        }

    async def fetch_return_eligibility(
        self,
        order_name: str,
        item_title: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch returnable fulfillments and line items for an order.
        Uses two sequential GraphQL queries: (1) get order ID by name, (2) returnableFulfillments(orderId).
        order_name: e.g. "#1001" or "1001"
        item_title: optional filter to match line item title (partial match after fetching).
        Returns dict with order_id, returnable_fulfillments, matching line items, and eligibility info.
        """
        if not self.shop or not self.access_token:
            return {"ok": False, "error": "Missing shop or access token"}
        order_query = (order_name or "").strip()
        if not order_query.startswith("#"):
            order_query = "#" + order_query
        query_string = f"name:{order_query}"
        base_url_2024_04 = f"https://{self.shop}/admin/api/{ORDER_LINE_ITEMS_API_VERSION}/graphql.json"

        # Query 1: Fetch order ID
        payload1 = {
            "query": GET_ORDER_ID_QUERY,
            "variables": {"query": query_string},
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp1 = await client.post(
                    base_url_2024_04,
                    json=payload1,
                    headers=self._headers(),
                )
                resp1.raise_for_status()
                data1 = resp1.json()
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": str(e), "status_code": e.response.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        errors1 = data1.get("errors")
        if errors1:
            return {"ok": False, "error": errors1[0] if errors1 else "GraphQL errors", "graphql_errors": errors1}

        orders_data = data1.get("data", {}).get("orders", {})
        edges = orders_data.get("edges") or []
        if not edges:
            return {"ok": True, "order_id": None, "returnable_fulfillments": [], "all_line_items": [], "matching_line_items": [], "message": "Order not found"}

        order_id = (edges[0].get("node") or {}).get("id")
        if not order_id:
            return {"ok": True, "order_id": None, "returnable_fulfillments": [], "all_line_items": [], "matching_line_items": [], "message": "Order not found"}

        # Query 2: Top-level returnableFulfillments(orderId)
        payload2 = {
            "query": GET_RETURNABLE_FULFILLMENTS_BY_ORDER_QUERY,
            "variables": {"orderId": order_id},
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp2 = await client.post(
                    base_url_2024_04,
                    json=payload2,
                    headers=self._headers(),
                )
                resp2.raise_for_status()
                data2 = resp2.json()
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": str(e), "status_code": e.response.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        errors2 = data2.get("errors")
        if errors2:
            return {"ok": False, "error": errors2[0] if errors2 else "GraphQL errors", "graphql_errors": errors2}

        returnable_fulfillments = []
        all_line_items: list[dict[str, Any]] = []

        rf_connection = data2.get("data", {}).get("returnableFulfillments") or {}
        for fe in (rf_connection.get("edges") or []):
            fn = fe.get("node") or {}
            returnable_fulfillments.append(fn)
            for le in (fn.get("returnableFulfillmentLineItems") or {}).get("edges") or []:
                ln = le.get("node") or {}
                fli = ln.get("fulfillmentLineItem") or {}
                line_item_node = fli.get("lineItem") or {}
                line_item_title = line_item_node.get("title") or ""
                all_line_items.append({
                    "fulfillmentLineItem": fli,
                    "quantity": ln.get("quantity"),
                    "lineItem": {"id": line_item_node.get("id"), "title": line_item_title},
                    "title": line_item_title,
                })

        matching_line_items = all_line_items
        if (item_title or "").strip():
            key = (item_title or "").strip().lower()
            matching_line_items = [li for li in all_line_items if key in (li.get("title") or "").lower()]

        return {
            "ok": True,
            "order_id": order_id,
            "returnable_fulfillments": returnable_fulfillments,
            "matching_line_items": matching_line_items,
            "all_line_items": all_line_items,
            "message": "Eligibility fetched" if (returnable_fulfillments or all_line_items) else "No returnable items",
        }

    async def fetch_order_line_items_for_ui(self, order_name: str) -> list[dict[str, Any]]:
        """
        Fetch an order's line items for the return UI using GraphQL Admin API 2024-04.
        Returns list of { id (gid), title, image (url), quantity_returnable }.
        """
        if not self.shop or not self.access_token:
            return []
        order_query = (order_name or "").strip()
        if not order_query.startswith("#"):
            order_query = "#" + order_query
        query_string = f"name:{order_query}"
        base_url_2024_04 = f"https://{self.shop}/admin/api/{ORDER_LINE_ITEMS_API_VERSION}/graphql.json"
        payload = {
            "query": GET_ORDER_LINE_ITEMS_UI_QUERY,
            "variables": {"query": query_string},
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    base_url_2024_04,
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []
        errors = data.get("errors")
        if errors:
            return []
        orders_data = data.get("data", {}).get("orders", {})
        edges = orders_data.get("edges") or []
        if not edges:
            return []
        node = edges[0].get("node") or {}
        line_edges = (node.get("lineItems") or {}).get("edges") or []
        result: list[dict[str, Any]] = []
        for le in line_edges:
            ln = le.get("node") or {}
            gid = ln.get("id") or ""
            title = ln.get("title") or ""
            qty = ln.get("quantity") or 0
            try:
                qty = int(qty) if qty is not None else 0
            except (TypeError, ValueError):
                qty = 0
            image_node = ln.get("image") or {}
            image_url = (image_node.get("url") or "").strip()
            result.append({
                "id": gid,
                "title": title,
                "image": image_url,
                "quantity_returnable": qty,
            })
        return result

    # Map UI reason labels to Shopify ReturnReason enum (2024-04)
    _REASON_MAP = {
        "too small": "SIZE_TOO_SMALL",
        "defective": "DEFECTIVE",
        "changed mind": "UNWANTED",
        "item arrived late": "OTHER",
    }

    RETURN_REQUEST_MUTATION = """
    mutation ReturnRequest($input: ReturnRequestInput!) {
      returnRequest(input: $input) {
        userErrors { field message }
        return { id status }
      }
    }
    """

    async def submit_return_request(
        self,
        order_name: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Execute Shopify returnRequest mutation. Items: list of { id (order LineItem GID), quantity, reason }.
        Resolves order LineItem id to FulfillmentLineItem id via returnable query, then submits the return.
        """
        if not self.shop or not self.access_token:
            return {"ok": False, "error": "Missing shop or access token"}
        if not items:
            return {"ok": False, "error": "No items to return"}
        order_query = (order_name or "").strip()
        if not order_query.startswith("#"):
            order_query = "#" + order_query
        # Fetch returnable items to map line item id -> fulfillment line item id
        eligibility = await self.fetch_return_eligibility(order_query, item_title=None)
        if not eligibility.get("ok"):
            return eligibility
        order_id = eligibility.get("order_id")
        if not order_id:
            return {"ok": False, "error": "Order not found or not returnable"}
        all_line_items = eligibility.get("all_line_items") or []
        # Build map: lineItem.id -> { fulfillment_line_item_id, quantity (max returnable) }
        line_to_fulfillment: dict[str, dict[str, Any]] = {}
        for li in all_line_items:
            line_item_node = li.get("lineItem") or {}
            line_item_id = (line_item_node.get("id") or "").strip()
            if not line_item_id:
                continue
            fli = li.get("fulfillmentLineItem") or {}
            fli_id = (fli.get("id") if isinstance(fli, dict) else getattr(fli, "id", None)) or ""
            if isinstance(fli_id, dict):
                fli_id = fli_id.get("id", "") or ""
            fli_id = str(fli_id).strip()
            qty = li.get("quantity") or 0
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                qty = 0
            if fli_id:
                line_to_fulfillment[line_item_id] = {"fulfillment_line_item_id": fli_id, "quantity": qty}
        return_line_items: list[dict[str, Any]] = []
        for it in items:
            line_item_id = (it.get("id") or "").strip()
            qty = it.get("quantity") or 1
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                qty = 1
            reason_raw = (it.get("reason") or "Other").strip().lower()
            return_reason = self._REASON_MAP.get(reason_raw) or "OTHER"
            if not line_item_id or line_item_id not in line_to_fulfillment:
                continue
            fli_id = line_to_fulfillment[line_item_id]["fulfillment_line_item_id"]
            max_qty = line_to_fulfillment[line_item_id]["quantity"]
            qty = min(max(1, qty), max_qty)
            entry: dict[str, Any] = {
                "fulfillmentLineItemId": fli_id,
                "quantity": qty,
                "returnReason": return_reason,
            }
            note = (it.get("customer_note") or "").strip()[:300]
            if note:
                entry["customerNote"] = note
            return_line_items.append(entry)
        if not return_line_items:
            return {"ok": False, "error": "No valid returnable items matched"}
        base_url_2024_04 = f"https://{self.shop}/admin/api/{ORDER_LINE_ITEMS_API_VERSION}/graphql.json"
        payload = {
            "query": self.RETURN_REQUEST_MUTATION,
            "variables": {
                "input": {
                    "orderId": order_id,
                    "returnLineItems": return_line_items,
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    base_url_2024_04,
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": str(e), "status_code": e.response.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        errors = data.get("errors")
        if errors:
            return {"ok": False, "error": errors[0] if errors else "GraphQL errors", "graphql_errors": errors}
        result = data.get("data", {}).get("returnRequest") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            return {"ok": False, "error": user_errors[0].get("message", "Validation failed"), "userErrors": user_errors}
        return_obj = result.get("return")
        return {"ok": True, "return": return_obj, "return_id": return_obj.get("id") if return_obj else None}
