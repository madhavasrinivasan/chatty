"""Shared Google GenAI client — API key or Vertex AI via gcloud ADC."""
from __future__ import annotations

import os

from google import genai

from app.core.config.config import settings

_client: genai.Client | None = None


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
    if settings.gemini_use_vertexai:
        if not settings.gemini_project:
            raise ValueError(
                "GEMINI_PROJECT is required when GEMINI_USE_VERTEXAI=true."
            )
        return genai.Client(
            vertexai=True,
            project=settings.gemini_project,
            location=settings.gemini_location,
        )
    if not settings.gemini_api_key:
        raise ValueError(
            "Set GEMINI_API_KEY or enable GEMINI_USE_VERTEXAI=true with GEMINI_PROJECT."
        )
    return genai.Client(api_key=settings.gemini_api_key)


def get_genai_client() -> genai.Client:
    global _client
    if _client is None:
        configure_gemini_env()
        _client = create_genai_client()
    return _client
