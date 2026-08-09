"""Subscription trial, wallet, and expiry helpers."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core import models as Models
from app.core.schema.applicationerror import ApplicationError
from app.core.services.token_tracker import usd_to_inr

TRIAL_DAYS = 14

# Paths that remain reachable when subscription is expired (JWT admin).
SUBSCRIPTION_GATE_ALLOWLIST_SUFFIXES = (
    "/admin/app/billing",
    "/admin/app/user",
    "/admin/app/onboarding-status",
    "/admin/app/demo-leads",
)


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def subscription_status(sub) -> str:
    if not sub:
        return "expired"
    end = _as_date(getattr(sub, "end_date", None))
    if end is None:
        return "expired"
    return "active" if end >= date.today() else "expired"


def days_left(sub) -> int:
    end = _as_date(getattr(sub, "end_date", None)) if sub else None
    if end is None:
        return 0
    return max(0, (end - date.today()).days)


async def get_latest_subscription(user_id: int):
    return (
        await Models.subscriptions.filter(user_id=user_id)
        .order_by("-created_at")
        .first()
    )


async def assert_subscription_active(user_id: int) -> None:
    sub = await get_latest_subscription(user_id)
    if subscription_status(sub) != "active":
        raise ApplicationError.SubscriptionExpired()


def path_is_subscription_allowlisted(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    for suffix in SUBSCRIPTION_GATE_ALLOWLIST_SUFFIXES:
        if p.endswith(suffix.rstrip("/")) or p == suffix:
            return True
    return False


async def provision_trial_and_wallet(user_id: int) -> dict[str, Any]:
    """Create 14-day trial subscription + empty wallet; link users.subscription_id."""
    today = date.today()
    end = today + timedelta(days=TRIAL_DAYS)
    sub = await Models.subscriptions.create(
        user_id=user_id,
        pack=Models.subscription_pack.trial,
        type=Models.subscription_type.monthly,
        start_date=today,
        end_date=end,
    )
    await Models.users.filter(id=user_id).update(subscription_id=sub.id)
    wallet, _ = await Models.wallets.get_or_create(
        user_id=user_id,
        defaults={"balance_inr": Decimal("0"), "currency": "INR"},
    )
    return {
        "subscription_id": sub.id,
        "pack": str(sub.pack.value if hasattr(sub.pack, "value") else sub.pack),
        "start_date": today.isoformat(),
        "end_date": end.isoformat(),
        "trial_days": TRIAL_DAYS,
        "wallet_id": wallet.id,
        "balance_inr": float(wallet.balance_inr or 0),
    }


async def ensure_wallet(user_id: int):
    wallet, _ = await Models.wallets.get_or_create(
        user_id=user_id,
        defaults={"balance_inr": Decimal("0"), "currency": "INR"},
    )
    return wallet


async def build_billing_payload(user_id: int) -> dict[str, Any]:
    sub = await get_latest_subscription(user_id)
    wallet = await ensure_wallet(user_id)
    status = subscription_status(sub)

    stores = await Models.ecom_store.filter(user_id=user_id).all()
    total_in = sum(int(getattr(s, "total_input_tokens", 0) or 0) for s in stores)
    total_out = sum(int(getattr(s, "total_output_tokens", 0) or 0) for s in stores)
    total_cost_usd = sum(float(getattr(s, "total_cost_usd", 0) or 0) for s in stores)
    total_cost_inr = usd_to_inr(total_cost_usd)
    balance_inr = float(wallet.balance_inr or 0)
    remaining_inr = max(0.0, round(balance_inr - total_cost_inr, 2))

    pack = None
    start_s = None
    end_s = None
    if sub:
        pack = str(sub.pack.value if hasattr(sub.pack, "value") else sub.pack)
        start_s = _as_date(sub.start_date)
        end_s = _as_date(sub.end_date)

    return {
        "subscription": {
            "id": sub.id if sub else None,
            "pack": pack,
            "start_date": start_s.isoformat() if start_s else None,
            "end_date": end_s.isoformat() if end_s else None,
            "days_left": days_left(sub),
            "status": status,
            "trial_days": TRIAL_DAYS,
        },
        "wallet": {
            "balance_inr": balance_inr,
            "currency": wallet.currency or "INR",
            "top_up_enabled": False,
        },
        "usage": {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "total_cost_usd": round(total_cost_usd, 6),
            "total_cost_inr": total_cost_inr,
        },
        "remaining_inr": remaining_inr,
    }


async def build_onboarding_status(user_id: int) -> dict[str, Any]:
    store = await Models.ecom_store.filter(user_id=user_id).order_by("-id").first()
    sub = await get_latest_subscription(user_id)
    end = _as_date(getattr(sub, "end_date", None)) if sub else None
    return {
        "has_store": bool(store),
        "store_type": str(getattr(store, "store_type", None) or "") if store else None,
        "store_name": getattr(store, "store_name", None) if store else None,
        "trial_ends_at": end.isoformat() if end else None,
        "subscription_status": subscription_status(sub),
    }
