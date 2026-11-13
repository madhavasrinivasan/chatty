"""Backfill product image embeddings for visual search.

Usage (inside the backend container):
  python scripts/backfill_image_embeddings.py --store-id 1
  python scripts/backfill_image_embeddings.py --store-id 1 --max-batches 100
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config.gemini_client import configure_gemini_env
from app.core.config.db import init_db, close_db
from app.admin.controller.appcontroller import AppController


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=1000)
    args = parser.parse_args()

    configure_gemini_env()
    await init_db()
    total_updated = 0
    try:
        for batch in range(max(1, args.max_batches)):
            result = await AppController.backfill_product_image_embeddings(
                args.store_id, limit=args.limit
            )
            total_updated += int(result.get("updated", 0))
            print(f"batch={batch + 1} result={result}", flush=True)
            if not result.get("remaining") or not result.get("processed"):
                break
    finally:
        await close_db()
    print(f"Done. Updated {total_updated} product image embeddings.")


if __name__ == "__main__":
    asyncio.run(main())
