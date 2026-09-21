"""
Jev (TypeSafe System One) intent classifier.

Jev decides routes / filters / confidence — it never writes shopper-facing text.
Gemini stays for prose (GENERAL_CHAT reply, synthesis) and as low-confidence fallback.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config.config import settings
from app.core.config.typesafe_client import typesafe_configured

ROUTE_OPTIONS = (
    "GENERAL_CHAT",
    "HYBRID_SEARCH",
    "ORDER_SUPPORT",
    "RETURN_REQUEST",
    "FOLLOW_UP_QUESTION",
    "CATALOG_AGENT",
)

_ORDER_NUM_RE = re.compile(
    r"(?:order\s*(?:number|no\.?|#)?\s*|#)\s*([A-Za-z0-9-]{3,})",
    re.IGNORECASE,
)
_BARE_ORDER_RE = re.compile(r"\b(\d{4,})\b")

_CHEAPEST_RE = re.compile(
    r"\b(cheapest|lowest\s+priced?|least\s+expensive|lowest\s+cost|lowest\s+price)\b",
    re.I,
)
_EXPENSIVE_RE = re.compile(
    r"\b(most\s+expensive|highest\s+priced?|priciest|highest\s+price)\b",
    re.I,
)
_NEWEST_RE = re.compile(r"\b(newest|latest|new\s+arrivals?)\b", re.I)
_PRICE_NOISE_RE = re.compile(
    r"\b(cheapest|lowest|priced?|expensive|most|least|highest|priciest|"
    r"show|me|the|what|is|are|a|an|item|items|product|products|find|get|"
    r"please|can|you|u|give|list|of)\b",
    re.I,
)
_KNOWLEDGE_RE = re.compile(
    r"\b("
    r"shippin\w*\s*polic\w*|shipping\s*polic\w*|return\s*polic\w*|refund\s*polic\w*|"
    r"privacy\s*polic\w*|terms\s*(and|&)?\s*conditions|terms\s*of\s*service|"
    r"faq|faqs|about\s*us|store\s*polic\w*|delivery\s*polic\w*|"
    r"how\s+long\s+(does|do|is)\s+(shipping|delivery)|"
    r"do\s+you\s+ship|shipping\s+info|delivery\s+info|"
    r"details?\s+(about|abt|on)\s+\w*\s*(shippin|shipping|return|refund|polic)"
    r")\b",
    re.I,
)
_BUDGET_MAX_RE = re.compile(
    r"(?:under|below|less\s+than|upto|up\s+to|max(?:imum)?|within)\s*(?:rs\.?|₹|inr)?\s*(\d+(?:\.\d+)?)",
    re.I,
)
_BUDGET_MIN_RE = re.compile(
    r"(?:over|above|at\s+least|more\s+than|from)\s*(?:rs\.?|₹|inr)?\s*(\d+(?:\.\d+)?)",
    re.I,
)
_COLLECTION_ASK_RE = re.compile(
    r"\b(best\s*sellers?|bestsellers?|new\s+arrivals?|featured|collection)\b",
    re.I,
)


@dataclass
class JevRouteResult:
    route: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    is_product_ask: float | None = None
    enough_info: float | None = None
    sort: str = "none"
    search_scope: str = "products"
    wants_discounts: bool = False
    needs_expander: bool = False
    needs_expander_noul: float | None = None
    extracted_order_number: str | None = None
    search_payload: dict[str, Any] | None = None
    follow_up_message: str | None = None
    latency_ms: float = 0.0
    skipped: bool = False
    reason: str = ""
    used_jev: bool = True


def jev_router_enabled() -> bool:
    if not typesafe_configured():
        return False
    return bool(getattr(settings, "jev_router_enabled", True))


def detect_sort_heuristic(message: str) -> str | None:
    text = message or ""
    if _CHEAPEST_RE.search(text):
        return "price_asc"
    if _EXPENSIVE_RE.search(text):
        return "price_desc"
    if _NEWEST_RE.search(text):
        return "newest"
    return None


def detect_knowledge_ask(message: str) -> bool:
    """True for shipping/return/privacy/FAQ — store_knowledge pages, not products or order tracking."""
    text = (message or "").strip()
    if not text:
        return False
    if _KNOWLEDGE_RE.search(text):
        return True
    lower = text.lower()
    # Loose typos: "shippin policy", "polic", "abt shipping"
    if "polic" in lower and any(w in lower for w in ("ship", "return", "refund", "privacy", "store")):
        return True
    if "faq" in lower:
        return True
    return False


def extract_price_bounds(message: str) -> tuple[float | None, float | None]:
    """Heuristic price_min / price_max from shopper text."""
    text = message or ""
    pmax = None
    pmin = None
    m = _BUDGET_MAX_RE.search(text)
    if m:
        try:
            pmax = float(m.group(1))
        except ValueError:
            pmax = None
    m = _BUDGET_MIN_RE.search(text)
    if m:
        try:
            pmin = float(m.group(1))
        except ValueError:
            pmin = None
    return pmin, pmax


def detect_collection_ask(message: str) -> str | None:
    """Return a collection filter string when the user names a collection theme."""
    text = (message or "").strip()
    if not text:
        return None
    m = _COLLECTION_ASK_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip().lower()
    if "best" in raw:
        return "best seller"
    if "new" in raw:
        return "new arrival"
    if raw == "featured":
        return "featured"
    return raw


def _chat_tail(chat_history: list | None, limit: int = 3) -> str:
    lines: list[str] = []
    for msg in (chat_history or [])[-limit:]:
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "user").strip().upper()
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:240]}")
    return "\n".join(lines)


def _build_state(
    message: str,
    chat_history: list | None,
    store_dna: str,
    *,
    has_orders: bool,
    has_cart: bool,
) -> str:
    dna = (store_dna or "").strip().replace("\n", " ")[:180]
    tail = _chat_tail(chat_history)
    return (
        f"STORE: {dna or '(unknown)'}\n"
        f"FLAGS: has_orders={'yes' if has_orders else 'no'} "
        f"has_cart={'yes' if has_cart else 'no'}\n"
        f"RECENT_CHAT:\n{tail or '(none)'}\n"
        f"LATEST_USER: {(message or '').strip()[:400]}"
    )


def extract_order_number(message: str) -> str | None:
    text = (message or "").strip()
    if not text:
        return None
    m = _ORDER_NUM_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_ORDER_RE.search(text)
    if m and any(k in text.lower() for k in ("order", "track", "shipping", "delivery", "status")):
        return m.group(1)
    return None


def _keywords_for_search(message: str, sort: str) -> str:
    raw = (message or "").strip()
    if sort in ("price_asc", "price_desc", "newest"):
        cleaned = _PRICE_NOISE_RE.sub(" ", raw)
        return " ".join(cleaned.split())[:80]
    return raw[:200]


def build_search_payload_from_jev(
    message: str,
    sort: str = "none",
    search_scope: str = "products",
) -> dict[str, Any]:
    """Pure Python search payload — no LLM. Sort/scope drive SQL."""
    scope = (search_scope or "products").strip().lower()
    if scope not in ("products", "knowledge", "all"):
        scope = "products"

    keywords = _keywords_for_search(message, sort)
    sort_column = None
    sort_order = None
    semantic = ""
    s = (sort or "none").lower()
    limit = 5
    if scope == "knowledge":
        # Prefer pages/custom; keep semantic so hybrid RAG can help
        keywords = (message or "").strip()[:160] or "shipping policy"
        # Normalize noisy typos toward searchable terms
        low = keywords.lower()
        if "shippin" in low or "shipping" in low:
            keywords = "shipping policy OR delivery policy OR shipping"
        elif "return" in low or "refund" in low:
            keywords = "return policy OR refund policy OR returns"
        semantic = (message or "").strip()[:200]
        limit = 8
    elif s == "price_asc":
        sort_column, sort_order = "price", "ASC"
        semantic = ""
        limit = 8
    elif s == "price_desc":
        sort_column, sort_order = "price", "DESC"
        semantic = ""
        limit = 8
    elif s == "newest":
        sort_column, sort_order = "created_at", "DESC"
        semantic = ""
    else:
        semantic = keywords[:120] if len(keywords.split()) >= 3 else ""

    pmin, pmax = extract_price_bounds(message or "")
    collection = detect_collection_ask(message or "")
    if pmax is not None and s == "none":
        # Budget without explicit "cheapest" → still price-asc among matches
        sort_column, sort_order = "price", "ASC"
        semantic = ""
        limit = max(limit, 8)

    rrf = {"keyword_weight": 0.5, "vector_weight": 0.5}
    if scope == "knowledge":
        rrf = {"keyword_weight": 0.6, "vector_weight": 0.4}
    elif sort_column == "price" or pmax is not None or pmin is not None:
        rrf = {"keyword_weight": 0.8, "vector_weight": 0.2}

    return {
        "search_keywords": keywords,
        "semantic_context": semantic,
        "sort_column": sort_column,
        "sort_order": sort_order,
        "limit": limit,
        "filters": {
            "color": None,
            "size": None,
            "category": None,
            "price_min": pmin,
            "price_max": pmax,
            "collection": collection,
        },
        "rrf_weights": rrf,
        "search_scope": scope,
    }


async def classify_intent_jev(
    message: str,
    chat_history: list | None = None,
    store_dna: str = "",
    *,
    has_orders: bool = False,
    has_cart: bool = False,
) -> JevRouteResult:
    """
    One TypeSafe call: Choice route + Noul guards + sort/discount Choices.
    Returns structured routing for code to act on.
    """
    if not jev_router_enabled():
        return JevRouteResult(
            route="HYBRID_SEARCH",
            confidence=0.0,
            skipped=True,
            reason="jev_disabled_or_unconfigured",
            used_jev=False,
        )

    threshold = float(getattr(settings, "jev_router_confidence_threshold", 0.55) or 0.55)
    state = _build_state(
        message,
        chat_history,
        store_dna,
        has_orders=has_orders,
        has_cart=has_cart,
    )
    sort_hint = detect_sort_heuristic(message or "")
    knowledge_hint = detect_knowledge_ask(message or "")

    t0 = time.perf_counter()
    try:
        from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

        async with AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key.strip(),
            model=settings.typesafe_default_model or "jev-latest",
        ) as client:
            # Cap Jev so a hung TypeSafe call can't stall the whole chat request
            # (and starve the FastAPI event loop for other sessions).
            jev_timeout_s = float(
                getattr(settings, "jev_router_timeout_s", 8.0) or 8.0
            )

            async def _system_one():
                return await client.system_one(
                    state,
                    {
                        "route": Choice(
                            instructions=(
                                "Pick the single best route for the LATEST_USER message "
                                "in an e-commerce store chat assistant.\n"
                                "- GENERAL_CHAT: greetings, thanks, small-talk only.\n"
                                "- HYBRID_SEARCH: product search, cheapest/most expensive, "
                                "pricing, catalog browse, sizes, AND store policies/FAQs/"
                                "shipping info/about-us (knowledge pages). "
                                "IMPORTANT: shipping policy, return policy, FAQ, 'details about shipping' "
                                "are ALWAYS HYBRID_SEARCH — never FOLLOW_UP or ORDER_SUPPORT. "
                                "'cheapest product' is ALWAYS HYBRID_SEARCH.\n"
                                "- ORDER_SUPPORT: tracking/status of an existing purchase only "
                                "(needs order language, not policy language).\n"
                                "- RETURN_REQUEST: customer wants to start a return/refund/exchange "
                                "of their purchase (not asking what the return policy says).\n"
                                "- FOLLOW_UP_QUESTION: only when the ask is too vague to search "
                                "(e.g. just 'something nice'). Never for policies or cheapest.\n"
                                "- CATALOG_AGENT: only for personalized multi-hop recommends that "
                                "explicitly need cart/past-order reasoning (e.g. 'based on my cart')."
                            ),
                            criteria={
                                "GENERAL_CHAT": "Greeting, thanks, chit-chat, off-topic.",
                                "HYBRID_SEARCH": (
                                    "Products, cheapest/expensive, prices, sizes, OR "
                                    "policies/FAQ/shipping/about-us knowledge pages."
                                ),
                                "ORDER_SUPPORT": "Order status, tracking of a purchase.",
                                "RETURN_REQUEST": "Start a return/refund/exchange of a purchase.",
                                "FOLLOW_UP_QUESTION": "Need a clarifying question before acting.",
                                "CATALOG_AGENT": (
                                    "Personalized recommend using cart and/or past orders."
                                ),
                            },
                        ),
                        "is_product_ask": Noul(
                            instructions=(
                                "Is the latest user message about shopping for / browsing / "
                                "comparing products or finding cheapest/expensive items "
                                "(not hello, not order tracking, not store policy/FAQ)?"
                            ),
                        ),
                        "is_knowledge_ask": Noul(
                            instructions=(
                                "Is the latest user asking about store policies, shipping info, "
                                "return/refund policy text, FAQs, about-us, or other non-product "
                                "store knowledge pages (not tracking a specific order)?"
                            ),
                        ),
                        "enough_info": Noul(
                            instructions=(
                                "Is there enough information to search the catalog/knowledge base "
                                "or look up an order without asking a clarifying question first? "
                                "Price-sort asks and policy asks (shipping policy, FAQ) have enough info (yes)."
                            ),
                        ),
                        "sort": Choice(
                            instructions=(
                                "If this is a product search, what sort intent? "
                                "'cheapest'/'lowest price' → price_asc. "
                                "'most expensive' → price_desc. "
                                "'newest' → newest. Otherwise none."
                            ),
                            criteria={
                                "none": "No special sort / relevance.",
                                "price_asc": "Cheapest / lowest price.",
                                "price_desc": "Most expensive / highest price.",
                                "newest": "Newest arrivals.",
                            },
                        ),
                        "search_scope": Choice(
                            instructions=(
                                "What should we search? "
                                "products = catalog items. "
                                "knowledge = policies/FAQ/pages/shipping info. "
                                "all = both."
                            ),
                            criteria={
                                "products": "Product catalog search.",
                                "knowledge": "Store policies, FAQs, shipping/about pages.",
                                "all": "Both products and knowledge pages.",
                            },
                        ),
                        "needs_expander": Noul(
                            instructions=(
                                "Do we need a store-DNA query expander before searching? "
                                "Yes if the product ask is messy/ambiguous and needs rewriting "
                                "into clean keywords, filters (color/size), or semantic vibe "
                                "(e.g. 'something cozy for my baby like the cart item', "
                                "'that red one but cheaper', vague browse with pronouns). "
                                "No if the ask is already simple enough to search directly: "
                                "cheapest/most expensive/newest sorts, clear short product "
                                "keywords, greetings, order tracking, or policy/FAQ knowledge asks."
                            ),
                        ),
                        "wants_discounts": Choice(
                            instructions=(
                                "Are they asking about discounts, coupons, deals, or promo codes?"
                            ),
                            criteria={"yes": None, "no": None},
                        ),
                    },
                )

            result = await asyncio.wait_for(_system_one(), timeout=jev_timeout_s)
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        print(f"[JEV] classify error ({ms:.0f}ms): {e}", flush=True)
        if sort_hint:
            return JevRouteResult(
                route="HYBRID_SEARCH",
                confidence=0.9,
                sort=sort_hint,
                search_scope="products",
                search_payload=build_search_payload_from_jev(
                    message or "", sort_hint, "products"
                ),
                latency_ms=ms,
                reason=f"jev_error_heuristic:{e}",
                used_jev=False,
            )
        if knowledge_hint:
            return JevRouteResult(
                route="HYBRID_SEARCH",
                confidence=0.9,
                search_scope="knowledge",
                search_payload=build_search_payload_from_jev(
                    message or "", "none", "knowledge"
                ),
                latency_ms=ms,
                reason=f"jev_error_knowledge:{e}",
                used_jev=False,
            )
        return JevRouteResult(
            route="HYBRID_SEARCH",
            confidence=0.0,
            latency_ms=ms,
            skipped=True,
            reason=f"jev_error:{e}",
            used_jev=False,
        )

    ms = (time.perf_counter() - t0) * 1000
    route_ans = result.choices["route"]
    route = str(route_ans.choice or "HYBRID_SEARCH")
    if route not in ROUTE_OPTIONS:
        route = "HYBRID_SEARCH"
    confidence = float(route_ans.confidence or 0.0)
    probs = dict(route_ans.probabilities or {})

    is_product = float(result.nouls["is_product_ask"].noul)
    is_knowledge = float(result.nouls["is_knowledge_ask"].noul)
    enough = float(result.nouls["enough_info"].noul)
    needs_exp_ans = result.nouls["needs_expander"]
    needs_exp_p = float(needs_exp_ans.noul)
    needs_exp_conf = float(getattr(needs_exp_ans, "confidence", 1.0) or 1.0)
    sort = str(result.choices["sort"].choice or "none")
    scope = str(result.choices["search_scope"].choice or "products")
    if scope not in ("products", "knowledge", "all"):
        scope = "products"
    wants_disc = str(result.choices["wants_discounts"].choice or "no").lower() == "yes"

    if sort_hint:
        sort = sort_hint
        scope = "products"
        if route in (
            "FOLLOW_UP_QUESTION",
            "ORDER_SUPPORT",
            "GENERAL_CHAT",
            "RETURN_REQUEST",
        ):
            route = "HYBRID_SEARCH"
            confidence = max(confidence, 0.9)

    if knowledge_hint or is_knowledge >= 0.55:
        scope = "knowledge"
        if route in ("FOLLOW_UP_QUESTION", "GENERAL_CHAT", "ORDER_SUPPORT"):
            route = "HYBRID_SEARCH"
            confidence = max(confidence, 0.9)
    elif scope == "knowledge":
        # Don't keep knowledge scope unless the ask is actually about policies/FAQ
        scope = "products"

    reason = "ok"
    if route == "GENERAL_CHAT" and is_product >= 0.65:
        route = "HYBRID_SEARCH"
        reason = "noul_product_override"
    elif route == "GENERAL_CHAT" and is_knowledge >= 0.55:
        route = "HYBRID_SEARCH"
        reason = "noul_knowledge_override"
    elif (
        route == "HYBRID_SEARCH"
        and enough < 0.35
        and confidence >= threshold
        and sort not in ("price_asc", "price_desc", "newest")
        and is_product < 0.85
        and is_knowledge < 0.55
        and not knowledge_hint
    ):
        route = "FOLLOW_UP_QUESTION"
        reason = "noul_not_enough_info"
    elif sort in ("price_asc", "price_desc", "newest") and route != "HYBRID_SEARCH":
        route = "HYBRID_SEARCH"
        reason = "sort_forces_hybrid"

    if route == "ORDER_SUPPORT" and (
        sort in ("price_asc", "price_desc")
        or _CHEAPEST_RE.search(message or "")
        or _EXPENSIVE_RE.search(message or "")
        or knowledge_hint
        or is_knowledge >= 0.55
    ):
        route = "HYBRID_SEARCH"
        reason = "not_order_support"

    # Gate Gemini QueryExpander — default ON for search; opt-out only for clear
    # price/newest sorts with no budget/collection (SQL seed is enough).
    pmin_h, pmax_h = extract_price_bounds(message or "")
    collection_h = detect_collection_ask(message or "")
    needs_expander = False
    if route in ("HYBRID_SEARCH", "CATALOG_AGENT"):
        if pmax_h is not None or pmin_h is not None or collection_h:
            needs_expander = True  # must extract price_max / collection cleanly
        elif scope in ("knowledge", "pages", "policy", "faq"):
            needs_expander = True  # policy/FAQ keywords + RRF
        elif sort in ("price_asc", "price_desc", "newest") and not (pmax_h or pmin_h):
            needs_expander = False  # clear SQL sort, no budget
        else:
            needs_expander = True  # product browse / vibe / CATALOG

    order_num = extract_order_number(message) if route == "ORDER_SUPPORT" else None
    search_payload = None
    follow_up = None
    if route in ("HYBRID_SEARCH", "CATALOG_AGENT"):
        search_payload = build_search_payload_from_jev(message, sort, scope)
    if route == "FOLLOW_UP_QUESTION":
        follow_up = (
            "Happy to help — what are you looking for "
            "(product type, size, or budget)?"
        )

    print(
        f"[JEV] route={route} conf={confidence:.2f} "
        f"product={is_product:.2f} knowledge={is_knowledge:.2f} enough={enough:.2f} "
        f"sort={sort} scope={scope} needs_expander={needs_expander} "
        f"(noul={needs_exp_p:.2f} conf={needs_exp_conf:.2f}) "
        f"discounts={wants_disc} ({ms:.0f}ms) reason={reason}",
        flush=True,
    )

    return JevRouteResult(
        route=route,
        confidence=confidence,
        probabilities=probs,
        is_product_ask=is_product,
        enough_info=enough,
        sort=sort,
        search_scope=scope,
        wants_discounts=wants_disc,
        needs_expander=needs_expander,
        needs_expander_noul=needs_exp_p,
        extracted_order_number=order_num,
        search_payload=search_payload,
        follow_up_message=follow_up,
        latency_ms=ms,
        skipped=False,
        reason=reason,
        used_jev=True,
    )


def jev_result_to_route_dict(jev: JevRouteResult) -> dict[str, Any]:
    """Shape compatible with RouterAndExpander.route_and_expand output."""
    out: dict[str, Any] = {
        "route": jev.route,
        "extracted_order_number": jev.extracted_order_number,
        "wants_discounts": jev.wants_discounts,
        "search_payload": jev.search_payload,
        "_usage": {
            "jev_router": {
                "input_tokens": 0,
                "output_tokens": 0,
            }
        },
        "_jev": {
            "confidence": jev.confidence,
            "probabilities": jev.probabilities,
            "is_product_ask": jev.is_product_ask,
            "enough_info": jev.enough_info,
            "sort": jev.sort,
            "search_scope": jev.search_scope,
            "needs_expander": jev.needs_expander,
            "needs_expander_noul": jev.needs_expander_noul,
            "latency_ms": jev.latency_ms,
            "reason": jev.reason,
        },
    }
    if jev.route == "FOLLOW_UP_QUESTION":
        out["follow_up_message"] = jev.follow_up_message
    if jev.needs_expander:
        out["needs_expander"] = True
    return out
