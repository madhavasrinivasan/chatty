"""
Tiktoken-based token estimates and Gemini-style cost helpers (per-component + totals).
~10% margin vs official Gemini usage_metadata; fast for billing dashboards.
"""
from __future__ import annotations

from dataclasses import dataclass

import tiktoken

# Default model used by ai_orchestrator / response_synthesis
DEFAULT_ORCHESTRATOR_MODEL = "gemini-2.5-flash"

# Gemini pricing per 1M tokens (USD). Extend as needed; 2.5 Flash uses Flash-tier placeholders.
GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-1.5-flash": {
        "input": 0.075,
        "output": 0.30,
    },
    "gemini-1.5-pro": {
        "input": 3.50,
        "output": 10.50,
    },
    "gemini-2.5-flash": {
        "input": 0.075,
        "output": 0.30,
    },
    "gemini-2.5-flash-lite": {
        "input": 0.10,
        "output": 0.40,
    },
}

# Approximate USD → INR for admin dashboard display (LLM list prices are in USD).
USD_TO_INR = 96.55


def usd_to_inr(amount_usd: float, rate: float = USD_TO_INR) -> float:
    return round(float(amount_usd or 0.0) * rate, 4)

_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class UsageResult:
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_enc.encode(str(text)))


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = DEFAULT_ORCHESTRATOR_MODEL,
) -> UsageResult:
    pricing = GEMINI_PRICING.get(model) or GEMINI_PRICING["gemini-2.5-flash"]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return UsageResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_usd=round(input_cost, 8),
        output_cost_usd=round(output_cost, 8),
        total_cost_usd=round(input_cost + output_cost, 8),
    )


def usage_result_to_dict(u: UsageResult) -> dict:
    return {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "input_cost_usd": u.input_cost_usd,
        "output_cost_usd": u.output_cost_usd,
        "total_cost_usd": u.total_cost_usd,
    }


def merge_token_components(*parts: dict[str, dict[str, int]] | None) -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = {}
    for d in parts:
        if not d:
            continue
        merged.update(d)
    return merged


def aggregate_components_to_totals(
    components: dict[str, dict[str, int]],
    model: str = DEFAULT_ORCHESTRATOR_MODEL,
) -> UsageResult:
    tin = sum(int(v.get("input_tokens", 0)) for v in components.values())
    tout = sum(int(v.get("output_tokens", 0)) for v in components.values())
    return calculate_cost(tin, tout, model)


def build_token_usage_payload(
    components: dict[str, dict[str, int]],
    model: str = DEFAULT_ORCHESTRATOR_MODEL,
) -> dict:
    u = aggregate_components_to_totals(components, model)
    return {
        "components": components,
        "totals": usage_result_to_dict(u),
        "model": model,
    }


def merge_token_usage_payload(
    base: dict | None,
    extra_components: dict[str, dict[str, int]] | None,
    model: str = DEFAULT_ORCHESTRATOR_MODEL,
) -> dict | None:
    if not base and not extra_components:
        return None
    comps: dict[str, dict[str, int]] = {}
    if base and base.get("components"):
        comps.update(base["components"])
    if extra_components:
        comps.update(extra_components)
    return build_token_usage_payload(comps, model)
