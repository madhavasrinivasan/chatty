"""TypeSafe / Jev client wrapper for structured decision checks."""
from __future__ import annotations

from typing import Any

from app.core.config.config import settings

_client: Any = None
_client_failed = False


def typesafe_configured() -> bool:
    return bool((settings.typesafe_api_key or "").strip())


def get_typesafe_client():
    """Lazy AsyncTypeSafeClient. Returns None if not configured or import fails."""
    global _client, _client_failed
    if _client_failed:
        return None
    if not typesafe_configured():
        return None
    if _client is not None:
        return _client
    try:
        from typesafe_sdk import AsyncTypeSafeClient

        _client = AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key.strip(),
            model=settings.typesafe_default_model or "jev-latest",
        )
        return _client
    except Exception as e:
        print(f"TypeSafe client unavailable: {e}", flush=True)
        _client_failed = True
        return None
