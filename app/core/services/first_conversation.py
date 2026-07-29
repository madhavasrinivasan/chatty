"""Personalized opening greeting when the chat widget loads (returning customers)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.config.gemini_client import get_genai_client
from app.core.schema.schema import FirstConvoGreetingOutput
from app.core.services.session_memory import (
    get_order_history_for_context,
    get_previous_session_summary_for_context,
    get_user_facts_for_context,
)

MODEL = "gemini-2.5-flash"

_GENERIC_FALLBACK = (
    "Hi there! Welcome to our store. I'm here to help you find the perfect products, "
    "check on an order, or answer any questions. What can I help you with today?"
)
_GENERIC_ACTIONS = [
    "What are your best sellers?",
    "Do you have any active discounts?",
    "I need help with an order",
]


def _build_personalized_prompt(
    *,
    store_dna: str,
    user_facts: str,
    order_history: str,
    previous_session_history: str,
    cart_token: str | None,
    cart_items: list[dict[str, Any]] | None,
    chat_history: list[dict[str, Any]],
) -> str:
    if cart_items:
        try:
            cart_str = json.dumps([{"title": i.get("title"), "quantity": i.get("quantity"), "price": i.get("price")} for i in cart_items], indent=2)
            cart_note = f"The customer has items in their cart right now:\n{cart_str}"
        except Exception:
            cart_note = "The customer currently has items in their cart (active cart session)."
    else:
        cart_note = (
            "The customer currently has items in their cart (active cart session)."
            if (cart_token or "").strip()
            else "No active cart token was provided."
        )

    current_chat = ""
    if chat_history:
        lines = []
        for m in chat_history[-6:]:
            role = (m.get("role") or "user").strip()
            content = (m.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        if lines:
            current_chat = "\n".join(lines)

    return f"""You are an exceptional, warm e-commerce sales associate greeting a returning customer when they open the chat widget.

STORE DNA (what we sell / our vibe):
{store_dna or "A friendly online store."}

KNOWN FACTS ABOUT THIS CUSTOMER (from past chats — use naturally, do not list robotically):
{user_facts or "(none yet)"}

THEIR PAST ORDERS (reference lightly if relevant — e.g. reorder, how they liked an item):
{order_history or "(no orders on file for this email)"}

SUMMARY OF PREVIOUS CHAT SESSIONS (pick up the thread — e.g. if they mentioned a kid, ask how the kid is doing; if they were browsing a theme, mention it):
{previous_session_history or "(first time chatting with us, or no prior sessions saved)"}

CART: {cart_note}

MESSAGES ALREADY IN THIS SESSION (usually empty on widget open):
{current_chat or "(none)"}

Write a opening message that:
1. Feels genuinely personal — like a great salesperson who remembers them, not a generic bot.
2. References ONE specific detail from facts, past orders, or prior sessions when available (never invent details).
3. If they have a cart, gently acknowledge they were shopping (without being pushy).
4. Is 2-4 short sentences, friendly Markdown, no bullet lists in the greeting itself.
5. Ends with a natural invitation to continue (question or offer to help).

Also provide exactly 2-3 short suggested_actions (under 8 words each) the customer might tap next.
Do NOT include "Add to cart", "Add … to cart", or any suggestion that asks the assistant to add a product to the cart — cart adds only work from the product card button, not from chat chips.

Output ONLY valid JSON matching the schema."""


async def generate_first_conversation_greeting(
    *,
    store_dna: str,
    shop_domain: str,
    access_token: str,
    customer_email: str | None,
    cart_token: str | None,
    chat_history: list[dict[str, Any]] | None,
    store_id: int | None,
    exclude_session_id: str | None = None,
    prefetched_user_facts: str | None = None,
    prefetched_previous_session_history: str | None = None,
    prefetched_order_history: str | None = None,
    cart_items: list[dict[str, Any]] | None = None,
    store: Any = None,
) -> tuple[str, list[str], bool, dict[str, Any]]:
    """
    Returns (general_answer, suggested_actions, personalized, context_used).
    Personalized path only runs when customer_email is set.
    """
    email = (customer_email or "").strip()
    history = chat_history or []
    context_used: dict[str, Any] = {
        "has_email": bool(email),
        "has_cart_token": bool((cart_token or "").strip()),
        "has_orders": False,
        "has_user_facts": False,
        "has_previous_sessions": False,
    }

    if not email:
        return _GENERIC_FALLBACK, list(_GENERIC_ACTIONS), False, context_used

    user_facts = prefetched_user_facts
    previous_session_history = prefetched_previous_session_history
    order_history = prefetched_order_history

    if user_facts is None or previous_session_history is None or order_history is None:
        if store_id:
            tasks = []
            if user_facts is None:
                tasks.append(get_user_facts_for_context(email, store_id))
            else:
                async def _dummy_facts(): return user_facts
                tasks.append(_dummy_facts())

            if previous_session_history is None:
                tasks.append(get_previous_session_summary_for_context(email, store_id, exclude_session_id=exclude_session_id))
            else:
                async def _dummy_prev(): return previous_session_history
                tasks.append(_dummy_prev())

            if order_history is None:
                tasks.append(
                    get_order_history_for_context(
                        shop_domain, access_token, email, limit=10, store=store
                    )
                )
            else:
                async def _dummy_orders(): return order_history
                tasks.append(_dummy_orders())

            user_facts, previous_session_history, order_history = await asyncio.gather(*tasks)
        elif store is not None or (shop_domain and access_token):
            if order_history is None:
                order_history = await get_order_history_for_context(
                    shop_domain, access_token, email, limit=10, store=store
                )

    context_used["has_user_facts"] = bool((user_facts or "").strip())
    context_used["has_previous_sessions"] = bool((previous_session_history or "").strip())
    context_used["has_orders"] = bool((order_history or "").strip())

    prompt = _build_personalized_prompt(
        store_dna=store_dna,
        user_facts=user_facts,
        order_history=order_history,
        previous_session_history=previous_session_history,
        cart_token=cart_token,
        cart_items=cart_items,
        chat_history=history,
    )

    try:
        client = get_genai_client()
        schema = FirstConvoGreetingOutput.model_json_schema()
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": schema,
                "thinking_config": {"thinking_budget": 0},
            },
        )
        raw = getattr(response, "text", None) or str(response)
        data = json.loads(raw)
        parsed = FirstConvoGreetingOutput.model_validate(data)
        actions = [a.strip() for a in (parsed.suggested_actions or []) if a and str(a).strip()][:3]
        if not actions:
            actions = list(_GENERIC_ACTIONS)
        answer = (parsed.general_answer or "").strip() or _GENERIC_FALLBACK
        return answer, actions, True, context_used
    except Exception as e:
        print(f"generate_first_conversation_greeting error: {e}")
        fallback = _GENERIC_FALLBACK
        if context_used["has_orders"]:
            fallback = (
                "Welcome back! Good to see you again. "
                "I can see you've shopped with us before — happy to help with a new order, "
                "tracking, or anything else. What brings you in today?"
            )
        return fallback, list(_GENERIC_ACTIONS), True, context_used
