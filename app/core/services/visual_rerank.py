"""Vision-model rerank for image search candidates.

The image embedding retrieves a shortlist; this module asks Gemini Flash to
compare the shopper's photo against the candidate product photos and reorder
them by true visual similarity (category, color, pattern, silhouette, fabric).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field
from google.genai import types

from app.core.config.config import settings
from app.core.config.gemini_client import get_genai_client
from app.core.services.webcrawler import Services


class _RankItem(BaseModel):
    index: int = Field(description="1-based candidate number.")
    score: float = Field(ge=0, le=100, description="Visual similarity 0-100.")
    reason: str = Field(default="", description="Short reason for the score.")


class _RankResponse(BaseModel):
    ranking: list[_RankItem] = Field(default_factory=list)


def _mime_for(data: bytes, fallback: str = "image/jpeg") -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    return fallback or "image/jpeg"


async def _load_candidate_images(candidates: list[dict[str, Any]]) -> list[tuple[dict, bytes, str]]:
    sem = asyncio.Semaphore(3)

    async def one(row: dict) -> tuple[dict, bytes, str] | None:
        async with sem:
            url = str(row.get("image_url") or "").strip()
            if not url:
                return None
            try:
                data, mime = await Services.resolve_image_bytes(None, url)
                if not data:
                    return None
                return row, data, _mime_for(data, mime)
            except Exception as exc:
                print(f"visual rerank image fetch failed: {exc}", flush=True)
                return None

    results = await asyncio.gather(*[one(row) for row in candidates], return_exceptions=True)
    return [r for r in results if isinstance(r, tuple)]


async def rerank_visual_matches(
    *,
    query_image_bytes: bytes | None,
    query_image_url: str | None,
    candidates: list[dict[str, Any]],
    top_k: int | None = None,
    max_candidates: int | None = None,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    if not bool(getattr(settings, "image_rerank_enabled", True)):
        return candidates[: int(top_k or 5)]

    top_k = int(top_k or getattr(settings, "image_rerank_top_k", 5) or 5)
    max_candidates = int(
        max_candidates or getattr(settings, "image_rerank_candidates", 8) or 8
    )
    shortlist = [r for r in candidates if isinstance(r, dict)][: max(1, max_candidates)]
    if not shortlist:
        return candidates[:top_k]

    if not query_image_bytes and query_image_url:
        try:
            query_image_bytes, _ = await Services.resolve_image_bytes(None, query_image_url)
        except Exception:
            query_image_bytes = None
    if not query_image_bytes:
        return candidates[:top_k]

    loaded = await _load_candidate_images(shortlist)
    if not loaded:
        return candidates[:top_k]

    parts: list[types.Part] = []
    parts.append(
        types.Part.from_text(
            text=(
                "You are a fashion visual-search matcher. Image 1 is the shopper's "
                "query photo. The following images are product candidates. Rank the "
                "candidates by visual similarity to the query: garment category, color, "
                "pattern/print, silhouette, sleeve/neckline, material and overall style. "
                "Prioritize exact visual similarity over generic category similarity. "
                "Return JSON with ranking items: index (1-based candidate number), "
                "score 0-100, reason (short). Include only score >= 35, best first, "
                "maximum 5. Do not invent candidates."
            )
        )
    )
    parts.append(types.Part.from_bytes(data=query_image_bytes, mime_type=_mime_for(query_image_bytes)))

    index_map: dict[int, dict[str, Any]] = {}
    for idx, (row, data, mime) in enumerate(loaded, start=1):
        index_map[idx] = row
        title = str(row.get("title") or "").strip()
        parts.append(types.Part.from_text(text=f"Candidate {idx}: {title}"))
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))

    try:
        client = get_genai_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=settings.gemini_router_model or "gemini-2.5-flash",
            contents=parts,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": _RankResponse.model_json_schema(),
                "thinking_config": {"thinking_budget": 0},
                "max_output_tokens": 900,
            },
        )
        data = json.loads(getattr(response, "text", None) or "{}")
        ranked = _RankResponse(**(data if isinstance(data, dict) else {}))
    except Exception as exc:
        print(f"visual rerank failed: {exc}", flush=True)
        return candidates[:top_k]

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in ranked.ranking:
        row = index_map.get(int(item.index))
        if not row:
            continue
        key = str(row.get("id") or row.get("shopify_product_id") or "")
        if key and key in seen_ids:
            continue
        if key:
            seen_ids.add(key)
        enriched = dict(row)
        enriched["visual_score"] = round(float(item.score), 1)
        enriched["visual_reason"] = (item.reason or "").strip()[:200]
        out.append(enriched)

    if not out:
        return candidates[:top_k]

    print(
        "🖼️ Vision rerank: "
        + ", ".join(
            f"#{i + 1}={r.get('visual_score')}" for i, r in enumerate(out[:top_k])
        ),
        flush=True,
    )
    return out[:top_k]
