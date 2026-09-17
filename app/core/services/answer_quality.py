"""
Extended-thinking quality gate using TypeSafe Jev Noul checks.

Only used when chatbot_customization.extended_thinking is True.
Does not write shopper-facing text — only yes/no quality signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config.config import settings
from app.core.config.typesafe_client import typesafe_configured


@dataclass
class AnswerQualityResult:
    good_enough: bool
    answer_accurate: float | None = None
    products_relevant: float | None = None
    skipped: bool = False
    reason: str = ""


def _product_titles(final_response: Any) -> list[str]:
    products = []
    if hasattr(final_response, "products"):
        products = final_response.products or []
    elif isinstance(final_response, dict):
        products = final_response.get("products") or []
    titles: list[str] = []
    for p in products:
        if isinstance(p, dict):
            t = (p.get("title") or p.get("name") or "").strip()
        else:
            t = (getattr(p, "title", None) or getattr(p, "name", None) or "").strip()
        if t:
            titles.append(t)
    return titles


def _answer_text(final_response: Any) -> str:
    if hasattr(final_response, "general_answer"):
        return (final_response.general_answer or "").strip()
    if isinstance(final_response, dict):
        return (final_response.get("general_answer") or "").strip()
    return ""


async def evaluate_answer_quality(
    user_query: str,
    final_response: Any,
    hybrid_results: list[dict] | None = None,
) -> AnswerQualityResult:
    """
    Ask Jev whether the answer is accurate enough and whether recommended
    products are a relevant match. Early success if 1–2 products look right.
    """
    threshold = float(settings.extended_thinking_noul_threshold or 0.55)
    titles = _product_titles(final_response)
    answer = _answer_text(final_response)
    n_products = len(titles)

    # Fast path without TypeSafe: 1+ product cards already shown → treat as success
    # so we never burn re-search tokens when the UI already has usable picks.
    if not typesafe_configured():
        if n_products >= 1:
            return AnswerQualityResult(
                good_enough=True,
                skipped=True,
                reason="typesafe_unconfigured_but_has_products",
            )
        return AnswerQualityResult(
            good_enough=False,
            skipped=True,
            reason="typesafe_unconfigured_no_products",
        )

    result_titles = []
    for r in (hybrid_results or [])[:8]:
        t = (r.get("title") or "").strip()
        if t:
            result_titles.append(t)

    state = (
        f"CUSTOMER ASK:\n{user_query or ''}\n\n"
        f"ASSISTANT ANSWER:\n{answer or '(empty)'}\n\n"
        f"RECOMMENDED PRODUCT TITLES:\n"
        + ("\n".join(f"- {t}" for t in titles) if titles else "(none)")
        + "\n\nSEARCH HIT TITLES (context):\n"
        + ("\n".join(f"- {t}" for t in result_titles) if result_titles else "(none)")
    )

    try:
        from typesafe_sdk import AsyncTypeSafeClient, Noul

        async with AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key.strip(),
            model=settings.typesafe_default_model or "jev-latest",
        ) as client:
            result = await client.system_one(
                state,
                {
                    "answer_accurate": Noul(
                        instructions=(
                            "Is this assistant answer accurate and helpful enough for the "
                            "customer's ask? Yes if it correctly addresses the request, "
                            "even if brief. No if it is off-topic, empty, contradictory, "
                            "or clearly wrong."
                        ),
                    ),
                    "products_relevant": Noul(
                        instructions=(
                            "Given the customer ask, are at least one or two of the "
                            "RECOMMENDED PRODUCT TITLES a relevant/good match? "
                            "Yes if even a single recommended product reasonably fits. "
                            "No if there are no products, or all recommended products "
                            "are a poor match."
                        ),
                    ),
                },
            )
        answer_p = float(result.nouls["answer_accurate"].noul)
        products_p = float(result.nouls["products_relevant"].noul)

        # Success rules (token-saving):
        # 1) One or two products look relevant → stop (do not re-search)
        # 2) Answer accurate enough AND at least one product shown
        # 3) FAQ/no-product case: answer accurate alone is enough
        good = False
        reason = "below_threshold"
        if n_products >= 1 and products_p >= threshold:
            good = True
            reason = "products_relevant"
        elif n_products >= 1 and answer_p >= threshold and products_p >= (threshold - 0.1):
            good = True
            reason = "answer_and_products_ok"
        elif n_products == 0 and answer_p >= max(threshold, 0.65):
            good = True
            reason = "answer_only_ok"
        elif n_products >= 1 and products_p >= 0.45 and answer_p >= 0.45:
            good = True
            reason = "soft_pass_has_products"

        return AnswerQualityResult(
            good_enough=good,
            answer_accurate=answer_p,
            products_relevant=products_p,
            reason=reason,
        )
    except Exception as e:
        print(f"evaluate_answer_quality TypeSafe error: {e}", flush=True)
        if n_products >= 1:
            return AnswerQualityResult(
                good_enough=True,
                skipped=True,
                reason=f"typesafe_error_has_products:{e}",
            )
        return AnswerQualityResult(
            good_enough=False,
            skipped=True,
            reason=f"typesafe_error_no_products:{e}",
        )
