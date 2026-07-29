import asyncio
from unittest.mock import AsyncMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.services.comez_service import ComezService


def test_transform_storefront_shape():
    """Storefront / viewallproducts shape: product_name, v_price, effective_price, display_image."""
    sample = {
        "product_name": 'Physical Product "The Band" T-Shirt',
        "product_id": "630",
        "slug": "physical-product-athe-banda-t-shirt",
        "display_image": ["1782301970078-189561821.webp"],
        "category_name": "Graphic shirt",
        "description": "<p>Celebrate the timeless legacy of The Band.</p>",
        "seo_title": "The Band Tee",
        "seo_description": "Graphic tee for music lovers",
        "variants": [
            {
                "id": "3115",
                "variant_name": "Small-green",
                "v_price": "19",
                "special_price": "0",
                "effective_price": "19",
                "stock_quantity": "47",
                "v_sku": "TheBandTShirt-SG",
                "barcode": "9050000Of251t",
                "status": "active",
            },
            {
                "id": "3116",
                "variant_name": "Small-gray",
                "v_price": "21",
                "special_price": "0",
                "effective_price": "21",
                "stock_quantity": "42",
                "v_sku": "TheBandTShirt-SA",
                "status": "active",
            },
        ],
    }
    prod = ComezService.transform_comez_product(sample)
    assert prod["shopify_product_id"] == "comez_630"
    assert prod["title"].startswith("Physical Product")
    assert prod["price"] == 19.0, f"first variant price expected 19, got {prod['price']}"
    assert prod["stock"] == 89
    assert prod["image_url"] and "1782301970078" in prod["image_url"]
    assert prod["variant_data"][0]["id"] == "3115"
    assert "Price: 19.0" in prod["content"]
    assert "Graphic shirt" in prod["content"]
    assert "Collections:" in prod["content"]
    assert "SKUs:" in prod["content"]
    assert "SEO Title:" in prod["content"]
    print("✅ Storefront-shape transform passed")


def test_transform_editor_shape_with_prices():
    """Editor getallproducts after price-field fix."""
    sample = {
        "id": "101",
        "name": "Classic Denim Jacket",
        "slug": "classic-denim-jacket",
        "description": "<p>A premium denim jacket.</p>",
        "media": ["jacket.jpg"],
        "vedor": "Levi's",
        "category_name": "Apparel",
        "variants": [
            {
                "id": "1",
                "variant_name": "M / Blue",
                "v_sku": "DENIM-JKT-M-BLU",
                "variant_quantity": 10,
                "stock_quantity": 10,
                "v_price": 2500,
                "special_price": 0,
                "effective_price": 2500,
            },
            {
                "id": "2",
                "variant_name": "L / Blue",
                "v_sku": "DENIM-JKT-L-BLU",
                "variant_quantity": 5,
                "stock_quantity": 5,
                "v_price": 2600,
                "special_price": 0,
                "effective_price": 2600,
            },
        ],
    }
    prod = ComezService.transform_comez_product(sample)
    assert prod["shopify_product_id"] == "comez_101"
    assert prod["price"] == 2500.0
    assert prod["stock"] == 15
    assert len(prod["variant_data"]) == 2
    assert "Levi's" in prod["content"]
    assert "Apparel" in prod["content"]
    print("✅ Editor-shape transform passed")


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
                    "courier_company_name": "Express Delivery",
                }
            ],
            "companyaddress": {"address": "123 Tech Park, Bangalore"},
            "gettaxstatus": True,
        },
    }

    class MockResponse:
        def __init__(self, json_data, status_code):
            self.json_data = json_data
            self.status_code = status_code
            self.content = True

        def json(self):
            return self.json_data

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
    test_transform_storefront_shape()
    test_transform_editor_shape_with_prices()
    asyncio.run(test_order_status_lookup())
