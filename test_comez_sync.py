import asyncio
from unittest.mock import AsyncMock, patch

# Configure sys.path so we can import app modules directly
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.services.comez_service import ComezService


async def test_product_fetch_and_transform():
    print("🧪 Running test: Comez Product Fetching & Transformation...")
    
    mock_payload = {
        "status": 200,
        "data": [
            {
                "id": "101",
                "name": "Classic Denim Jacket",
                "slug": "classic-denim-jacket",
                "description": "<p>A premium denim jacket.</p>",
                "price": 2500,
                "media": "jacket.jpg",
                "vedor": "Levi's",
                "category_name": "Apparel",
                "sku": "DENIM-JKT",
                "variant_name": "M / Blue",
                "v_sku": "DENIM-JKT-M-BLU",
                "variant_quantity": 10,
                "v_price": 2500
            },
            {
                "id": "101",
                "name": "Classic Denim Jacket",
                "slug": "classic-denim-jacket",
                "description": "<p>A premium denim jacket.</p>",
                "price": 2500,
                "media": "jacket.jpg",
                "vedor": "Levi's",
                "category_name": "Apparel",
                "sku": "DENIM-JKT",
                "variant_name": "L / Blue",
                "v_sku": "DENIM-JKT-L-BLU",
                "variant_quantity": 5,
                "v_price": 2600
            }
        ]
    }

    class MockResponse:
        def __init__(self, json_data, status_code):
            self.json_data = json_data
            self.status_code = status_code
            self.content = True

        def json(self):
            return self.json_data

    # Mock client.get
    mock_get = AsyncMock(return_value=MockResponse(mock_payload, 200))

    with patch("httpx.AsyncClient.get", mock_get):
        products = await ComezService.fetch_all_products("my-store")
        
        assert len(products) == 1, f"Expected 1 grouped product, got {len(products)}"
        
        prod = products[0]
        assert prod["shopify_product_id"] == "comez_101", f"Expected comez_101, got {prod['shopify_product_id']}"
        assert prod["handle"] == "classic-denim-jacket"
        assert prod["title"] == "Classic Denim Jacket"
        assert prod["price"] == 2500.0, f"Expected min price 2500.0, got {prod['price']}"
        assert prod["stock"] == 15, f"Expected sum of stock to be 15, got {prod['stock']}"
        assert len(prod["variant_data"]) == 2, "Expected 2 variants"
        
        assert "Levi's" in prod["content"]
        assert "Apparel" in prod["content"]
        assert "DENIM-JKT-M-BLU" in prod["content"]
        assert "M / Blue" in prod["content"]
        
        print("✅ Product fetch & transform test passed!")


async def test_order_status_lookup():
    print("🧪 Running test: Comez Order Status Lookup...")

    mock_payload = {
        "status": 200,
        "data": {
            "orders": [
                {
                    "id": "1245",
                    "name": "Classic Denim Jacket",
                    "product_name": "Classic Denim Jacket",
                    "variant_name": "M / Blue",
                    "quantity": "2",
                    "price": 2500,
                    "delivery_status": "delivered",
                    "payment_status": "paid",
                    "tracking_id": "TRACK12345",
                    "courier_company_name": "Express Delivery"
                }
            ],
            "companyaddress": {
                "address": "123 Tech Park, Bangalore"
            },
            "gettaxstatus": True
        }
    }

    class MockResponse:
        def __init__(self, json_data, status_code):
            self.json_data = json_data
            self.status_code = status_code
            self.content = True

        def json(self):
            return self.json_data

    # Mock client.post
    mock_post = AsyncMock(return_value=MockResponse(mock_payload, 200))

    with patch("httpx.AsyncClient.post", mock_post):
        status = await ComezService.get_order_status("my-store", "my-token", "1245")
        
        assert status["found"] is True
        assert status["order_name"] == "#1245"
        assert status["fulfillment_status"] == "fulfilled"
        assert status["financial_status"] == "paid"
        assert len(status["line_items"]) == 1
        assert status["line_items"][0]["quantity"] == 2
        
        tracking = status["shipping_payload"]["fulfillments"][0]
        assert tracking["tracking_number"] == "TRACK12345"
        assert tracking["tracking_company"] == "Express Delivery"
        
        print("✅ Order status lookup test passed!")


if __name__ == "__main__":
    asyncio.run(test_product_fetch_and_transform())
    asyncio.run(test_order_status_lookup())
