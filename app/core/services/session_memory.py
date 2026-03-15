"""
Dual-Memory chat: persist chat transcripts, extract user facts via LLM, and fetch context for the orchestrator.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.config.config import settings
from app.core.models.models import ChatTranscript, UserMemorySummary
from app.core.schema.schema import SyncSessionRequest
from app.core.services.shopify_service import get_orders_by_customer_email

try:
    from google import genai
    _genai_client: genai.Client | None = None

    def _get_genai_client() -> genai.Client:
        global _genai_client
        if _genai_client is None:
            _genai_client = genai.Client(api_key=settings.gemini_api_key)
        return _genai_client
except Exception:
    _get_genai_client = None  # type: ignore

MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "Analyze this chat transcript and extract ONLY permanent, highly relevant facts "
    "about the user's preferences, sizes, baby details, or explicit dislikes. "
    "Ignore pleasantries, greetings, or temporary questions. "
    "Output a strict JSON array of strings. Example: [\"User prefers muslin fabric\", \"Baby is 3 months old\"]."
)


def _format_history_for_llm(chat_history: list[dict[str, Any]]) -> str:
    """Turn list of {role, content} into a single string for the LLM."""
    lines = []
    for msg in chat_history:
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(empty)"


async def extract_facts_from_transcript(chat_history: list[dict[str, Any]]) -> list[str]:
    """
    Use the LLM to extract permanent, relevant facts from the transcript.
    Returns a list of fact strings, e.g. ["User prefers muslin fabric", "Baby is 3 months old"].
    """
    if not chat_history:
        return []
    try:
        if _get_genai_client is None:
            return []
        client = _get_genai_client()
        transcript = _format_history_for_llm(chat_history)
        prompt = f"{SYSTEM_PROMPT}\n\nTranscript:\n{transcript}\n\nOutput ONLY a JSON array of strings, no markdown."
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
            },
        )
        raw = getattr(response, "text", None) or str(response)
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if x]
        return []
    except Exception as e:
        print(f"extract_facts_from_transcript error: {e}")
        return []


async def get_user_facts_for_context(user_email: str, store_id: int) -> str:
    """Compile permanent facts for this user/store into a single string for prompts."""
    if not (user_email or "").strip():
        return ""
    try:
        rows = await UserMemorySummary.filter(
            user_email=user_email.strip(),
            store_id=store_id,
        ).order_by("-created_at").limit(50)
        facts = [r.fact for r in rows if (r.fact or "").strip()]
        return " ".join(facts) if facts else ""
    except Exception as e:
        print(f"get_user_facts_for_context error: {e}")
        return ""


async def get_previous_session_summary_for_context(
    user_email: str,
    store_id: int,
    exclude_session_id: str | None = None,
    max_sessions: int = 5,
) -> str:
    """Summarize past chat sessions for this user/store (excluding current session) for prompts."""
    if not (user_email or "").strip():
        return ""
    try:
        q = ChatTranscript.filter(
            user_email=user_email.strip(),
            store_id=store_id,
        )
        if exclude_session_id:
            q = q.exclude(session_id=exclude_session_id)
        sessions = await q.order_by("-created_at").limit(max_sessions)
        parts = []
        for s in sessions:
            raw = getattr(s, "raw_history", None) or []
            if not raw:
                continue
            snippet = " | ".join(
                (str(m.get("content", ""))[:80] for m in raw[-2:] if isinstance(m, dict))
            )
            if snippet:
                parts.append(f"Session {s.session_id}: {snippet}")
        return " ".join(parts) if parts else ""
    except Exception as e:
        print(f"get_previous_session_summary_for_context error: {e}")
        return ""


async def get_order_history_for_context(
    shop_domain: str,
    access_token: str,
    customer_email: str,
    limit: int = 20,
) -> str:
    """Format past orders for this customer as a string for prompts (e.g. 'Order #102: Muslin Jabla 0-3M Elephant')."""
    if not (customer_email or "").strip() or not shop_domain or not access_token:
        return ""
    try:
        orders = await asyncio.to_thread(
            get_orders_by_customer_email,
            shop_domain,
            access_token,
            customer_email,
            limit=limit,
        )
        lines = []
        print(f"Orders: {orders}")
        for o in orders:
            name = o.get("order_name") or ""
            summary = o.get("line_items_summary") or "—"
            lines.append(f"Order {name}: {summary}")
        return " ".join(lines) if lines else ""
    except Exception as e:
        print(f"get_order_history_for_context error: {e}")
        return ""


def _json_safe_history(chat_history: list) -> list:
    """Ensure chat_history is JSON-serializable for Tortoise JSONField (list of dicts, plain types)."""
    try:
        return json.loads(json.dumps(chat_history, default=str))
    except (TypeError, ValueError):
        return [{"role": str(m.get("role", "user")), "content": str(m.get("content", ""))} for m in chat_history if isinstance(m, dict)]


async def process_and_save_session(payload: SyncSessionRequest) -> None:
    """
    Background task: save transcript to ChatTranscript; if user_email and store_id present and history long enough,
    extract facts via LLM and save each to UserMemorySummary.
    chat_history items may have role, content, and optionally products, urls, order_status, suggested_actions.
    """
    store_id = payload.store_id if payload.store_id is not None else 0
    raw_history = _json_safe_history(payload.chat_history)
    # 1. Save full chat history to ChatTranscript (upsert by session_id)
    await ChatTranscript.update_or_create(
        defaults={
            "store_id": store_id,
            "user_email": payload.user_email,
            "raw_history": raw_history,
        },
        session_id=payload.session_id,
    )

    # 2. If user_email and store_id provided and enough messages, summarize once and save as a whole (replace previous facts)
    if (
        payload.user_email
        and payload.store_id is not None
        and len(payload.chat_history) > 6
    ):
        facts = await extract_facts_from_transcript(payload.chat_history)
        # Replace all existing facts for this user+store with this consolidated set (one summarized whole per sync)
        await UserMemorySummary.filter(
            user_email=payload.user_email,
            store_id=payload.store_id,
        ).delete()
        for fact in facts:
            if not fact:
                continue
            await UserMemorySummary.create(
                user_email=payload.user_email,
                store_id=payload.store_id,
                fact=fact,
            )
