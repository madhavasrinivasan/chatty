"""Small in-process fixed-window rate limiter for public widget endpoints."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

_lock = asyncio.Lock()
_buckets: dict[str, deque[float]] = defaultdict(deque)


async def allow(key: str, *, limit: int = 60, window_seconds: int = 60) -> bool:
    """Return True when `key` is under `limit` requests in the window."""
    if not key:
        key = "anonymous"
    if limit <= 0:
        return True
    now = time.monotonic()
    async with _lock:
        bucket = _buckets[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        # Keep the dict from growing forever with one-off keys.
        if len(_buckets) > 10_000:
            for stale_key in list(_buckets.keys())[:1000]:
                if not _buckets[stale_key]:
                    _buckets.pop(stale_key, None)
        return True
