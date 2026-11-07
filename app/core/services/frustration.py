"""Frustration detection + automatic human-handoff ticket creation."""
from __future__ import annotations

import re
import time
from typing import Any

from app.core.models.models import ChatSession, TicketsRaised

_HUMAN_REQUEST = re.compile(
    r"\b(?:speak|talk|chat)\s+(?:to|with)\s+(?:a|an|the)?\s*"
    r"(?:human|person|agent|representative|manager|someone|support)\b",
    re.IGNORECASE,
)
_STRONG = re.compile(
    r"\b(?:useless|ridiculous|unacceptable|frustrat\w*|annoy\w*|furious|"
    r"fed up|sick of|hate (?:this|you|the store)|not helpful|"
    r"nobody help\w*|waste of time|worst(?!\s+case)|"
    r"terrible (?:service|experience|store)|angry (?:with|about|at))\b",
    re.IGNORECASE,
)
_REPEAT = re.compile(
    r"\b(?:still (?:not|wrong|broken|waiting)|again|already (?:told|said)|"
    r"how many times|as i said)\b",
    re.IGNORECASE,
)
_PROFANITY = re.compile(r"\b(?:fuck\w*|shit\w*|damn|bullshit)\b", re.IGNORECASE)
_NEGATIVE_SEEDS = (
    "not",
    "still",
    "again",
    "worst",
    "useless",
    "ridiculous",
    "help",
    "never",
    "wrong",
    "broken",
)


def detect_frustration(message: str, chat_history: list | None = None) -> bool:
    """Cheap deterministic signal — no extra LLM call on the hot path."""
    text = (message or "").strip()
    if not text:
        return False
    if _HUMAN_REQUEST.search(text):
        return True
    if _PROFANITY.search(text):
        return True
    if _STRONG.search(text):
        return True

    repeat = bool(_REPEAT.search(text))
    if repeat and any(seed in text.lower() for seed in _NEGATIVE_SEEDS):
        return True

    # Two frustrated turns within the recent window is enough.
    if chat_history:
        negative_turns = 0
        for msg in chat_history[-6:]:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            prior = str(msg.get("content") or "")
            if _STRONG.search(prior) or _PROFANITY.search(prior) or (
                _REPEAT.search(prior) and any(s in prior.lower() for s in _NEGATIVE_SEEDS)
            ):
                negative_turns += 1
        if negative_turns >= 2:
            return True
    return False


async def maybe_escalate_to_human(
    *,
    message: str,
    session_id: str,
    store: Any,
    customer_email: str | None,
    chat_history: list | None = None,
) -> str | None:
    """
    Create one ticket per session and flag the session for human review.
    Returns the created ticket id, or None when no escalation happened.
    """
    if not detect_frustration(message, chat_history):
        return None
    if store is None or not getattr(store, "user_id", None):
        return None

    session = await ChatSession.filter(id=session_id).first()
    if session and session.needs_human:
        return None

    ticket_code = f"CHAT-{str(session_id)[:8].upper()}-{int(time.time())}"
    shop = getattr(store, "store_name", "") or "store"
    email = (customer_email or "").strip() or f"session-{str(session_id)[:8]}@chat.local"
    description = (
        f"Automatic escalation triggered by frustrated chat message.\n"
        f"Session: {session_id}\n"
        f"Store: {shop}\n"
        f"Shopper: {email}\n"
        f"Message: {message[:1500]}"
    )
    try:
        await TicketsRaised.create(
            user_id=int(store.user_id),
            chatbot_id=getattr(store, "chatbot_id", None),
            ticket_id=ticket_code,
            ticket_subject=f"Chat escalation · {shop}",
            user_email=email,
            user_name=None,
            user_phone="",
            user_address=None,
            ticket_description=description,
            ticket_status="open",
        )
        await ChatSession.filter(id=session_id).update(needs_human=True)
        store.total_escalations = int(getattr(store, "total_escalations", 0) or 0) + 1
        await store.save(update_fields=["total_escalations"])
        print(f"🚨 Chat escalated to human · ticket={ticket_code} session={session_id}", flush=True)
        return ticket_code
    except Exception as exc:
        print(f"Chat escalation skipped: {exc}", flush=True)
        return None
