"""Shared Google GenAI client — API key or Vertex AI via gcloud ADC."""
from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai import types

from app.core.config.config import settings

_client: genai.Client | None = None


def _build_http_options() -> types.HttpOptions:
    """
    Timeout + retry policy for every Gemini call.

    - timeout caps a single request so a hung API call can't stall the whole
      chat request (this was the cause of "stuck midway" / 8s+ router calls).
    - retry_options auto-retries transient 429/5xx errors with backoff.
    """
    timeout_ms = int(getattr(settings, "gemini_timeout_ms", 20000) or 20000)
    attempts = max(1, int(getattr(settings, "gemini_max_retries", 2) or 1))
    return types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(
            attempts=attempts,
            http_status_codes=[429, 500, 502, 503, 504],
        ),
    )


def configure_gemini_env() -> None:
    """Env vars used by LightRAG when running in Vertex/ADC mode."""
    if settings.gemini_use_vertexai:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.gemini_project
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.gemini_location
    else:
        os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
        if settings.gemini_api_key:
            os.environ["GEMINI_API_KEY"] = settings.gemini_api_key


def create_genai_client() -> genai.Client:
    http_options = _build_http_options()
    if settings.gemini_use_vertexai:
        if not settings.gemini_project:
            raise ValueError(
                "GEMINI_PROJECT is required when GEMINI_USE_VERTEXAI=true."
            )
        return genai.Client(
            vertexai=True,
            project=settings.gemini_project,
            location=settings.gemini_location,
            http_options=http_options,
        )
    if not settings.gemini_api_key:
        raise ValueError(
            "Set GEMINI_API_KEY or enable GEMINI_USE_VERTEXAI=true with GEMINI_PROJECT."
        )
    return genai.Client(api_key=settings.gemini_api_key, http_options=http_options)


def get_genai_client() -> genai.Client:
    global _client
    if _client is None:
        configure_gemini_env()
        _client = create_genai_client()
    return _client


def generate_content_text(
    model: str,
    contents: Any,
    config: dict | None = None,
    *,
    empty_retries: int = 1,
) -> str:
    """
    Call Gemini and return the response text, retrying when the model returns an
    empty body (a 200 with no text — e.g. transient overload/safety).

    Network/timeout errors and transient 429/5xx are already retried by the
    client's http_options; this guards the separate "empty 200" failure mode that
    was silently degrading the router to its fallback payload. Returns "" if every
    attempt is empty; hard exceptions propagate to the caller's own fallback.
    """
    client = get_genai_client()
    text = ""
    for _ in range(max(1, empty_retries + 1)):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config or {},
        )
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return text
    return text
