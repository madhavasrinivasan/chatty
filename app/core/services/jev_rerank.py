"""
Jev Noul re-ranker — TypeSafe cookbook pattern.

DB/hybrid search builds a shortlist; Jev scores each candidate vs the ask
with a Noul (fit probability), then we keep top-K and drop weak matches
before Gemini writes the shopper-facing answer (smaller prompt, better picks).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.config.config import settings
from app.core.config.typesafe_client import typesafe_configured


def jev_rerank_enabled() -> bool:
    if not typesafe_configured():
        return False
    return bool(getattr(settings, "jev_rerank_enabled", True))


def _snippet(row: dict[str, Any], limit: int = 280) -> str:
    title = (row.get("title") or "").strip()
    content = (row.get("content") or "").strip().replace("\n", " ")
    price = row.get("price")
    parts = [f"Title: {title}"]
    if price is not None:
        parts.append(f"Price: {price}")
    if content:
        parts.append(f"Desc: {content[:limit]}")
    return "\n".join(parts)


async def rerank_products_with_jev(
    user_query: str,
    rows: list[dict[str, Any]],
    *,
    top_k: int | None = None,
    max_candidates: int | None = None,
    min_noul: float | None = None,
) -> list[dict[str, Any]]:
    """
    Score shortlist with one Noul per product (parallel), sort high→low,
    drop weak matches, return top_k. On failure/skip, return original rows truncated.
    """
    if not rows:
        return []

    k = int(top_k if top_k is not None else getattr(settings, "jev_rerank_top_k", 8) or 8)
    max_c = int(
        max_candidates
        if max_candidates is not None
        else getattr(settings, "jev_rerank_max_candidates", 12)
        or 12
    )
    thresh = float(
        min_noul
        if min_noul is not None
        else getattr(settings, "jev_rerank_min_noul", 0.25)
        or 0.25
    )

    if not jev_rerank_enabled():
        return rows[:k]

    candidates = rows[:max_c]
    t0 = time.perf_counter()

    try:
        from typesafe_sdk import AsyncTypeSafeClient, Noul

        async with AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key.strip(),
            model=settings.typesafe_default_model or "jev-latest",
        ) as client:

            async def _score(row: dict[str, Any]) -> tuple[dict[str, Any], float]:
                state = (
                    f"CUSTOMER ASK:\n{(user_query or '').strip()[:400]}\n\n"
                    f"CANDIDATE PRODUCT:\n{_snippet(row)}"
                )
                try:
                    result = await client.system_one(
                        state,
                        {
                            "fit": Noul(
                                instructions=(
                                    "Does this candidate product reasonably match what "
                                    "the customer is asking for? Yes if it is a relevant "
                                    "product to recommend for this ask. No if it is "
                                    "off-topic, unrelated, or a poor match."
                                ),
                            ),
                        },
                    )
                    return row, float(result.nouls["fit"].noul)
                except Exception as e:
                    print(f"[JEV-RERANK] score error: {e}", flush=True)
                    return row, 0.0

            scored = await asyncio.gather(*[_score(r) for r in candidates])
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        print(f"[JEV-RERANK] unavailable ({ms:.0f}ms): {e} — using DB order", flush=True)
        return rows[:k]

    ranked = sorted(scored, key=lambda x: x[1], reverse=True)
    kept: list[dict[str, Any]] = []
    for row, noul in ranked:
        if noul < thresh and kept:
            continue
        enriched = dict(row)
        enriched["jev_fit"] = round(noul, 3)
        kept.append(enriched)
        if len(kept) >= k:
            break

    # Always keep at least 1–3 so synthesis has something
    if not kept:
        for row, noul in ranked[: min(3, k)]:
            enriched = dict(row)
            enriched["jev_fit"] = round(noul, 3)
            kept.append(enriched)

    ms = (time.perf_counter() - t0) * 1000
    preview = ", ".join(
        f"{(r.get('title') or '')[:28]}={r.get('jev_fit')}" for r in kept[:5]
    )
    print(
        f"[JEV-RERANK] {len(candidates)}→{len(kept)} in {ms:.0f}ms | {preview}",
        flush=True,
    )
    return kept
