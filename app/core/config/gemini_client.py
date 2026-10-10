"""Shared Google GenAI client — API key or Vertex AI via gcloud ADC."""
from __future__ import annotations

import os
from typing import Any
import httpx
import google.auth
from google.oauth2 import service_account
import google.auth.transport.requests

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
    """Env vars used by LightRAG / google-genai ADC when running in Vertex mode."""
    creds = (settings.google_application_credentials or "").strip()
    if creds:
        # Resolve relative paths from the backend working directory.
        path = os.path.abspath(creds)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"GOOGLE_APPLICATION_CREDENTIALS file not found: {path}"
            )
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

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


_embedding_client: genai.Client | None = None


def get_genai_embedding_client() -> genai.Client:
    """
    Dedicated client for embedding models.
    Embedding models on Vertex AI require specific regional endpoints (e.g. us-central1)
    and do not exist in the 'global' location.
    """
    global _embedding_client
    if _embedding_client is None:
        configure_gemini_env()
        http_options = _build_http_options()
        if settings.gemini_use_vertexai:
            loc = getattr(settings, "gemini_embedding_location", "us-central1") or "us-central1"
            if settings.gemini_location and settings.gemini_location != "global":
                loc = settings.gemini_location
            _embedding_client = genai.Client(
                vertexai=True,
                project=settings.gemini_project,
                location=loc,
                http_options=http_options,
            )
        else:
            _embedding_client = get_genai_client()
    return _embedding_client


_vertex_credentials = None


def get_vertex_access_token() -> str:
    """Extract or refresh OAuth2 Bearer token from GCP Service Account credentials."""
    global _vertex_credentials
    creds_path = (settings.google_application_credentials or "").strip()
    try:
        if creds_path:
            path = os.path.abspath(creds_path)
            if os.path.isfile(path):
                if _vertex_credentials is None:
                    _vertex_credentials = service_account.Credentials.from_service_account_file(
                        path,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    )
                if not _vertex_credentials.valid:
                    req = google.auth.transport.requests.Request()
                    _vertex_credentials.refresh(req)
                return _vertex_credentials.token or ""

        # Fallback to application default credentials
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not creds.valid:
            req = google.auth.transport.requests.Request()
            creds.refresh(req)
        return creds.token or ""
    except Exception as e:
        print(f"⚠️ Error obtaining Vertex access token: {e}", flush=True)
        return ""


async def call_vertex_embed_content(
    model: str,
    parts: list[dict],
    output_dimensionality: int = 768,
    location: str | None = None,
) -> list[float] | None:
    """
    Directly call the official :embedContent REST endpoint.
    Bypasses SDK issues where :predict is incorrectly called and non-text parts are stripped.
    Handles Vertex AI (us-central1, global) and Google AI Studio automatically.
    """
    body = {
        "content": {"parts": parts},
        "outputDimensionality": output_dimensionality,
    }

    # Mode 1: Google AI Studio (API key mode)
    if not settings.gemini_use_vertexai:
        api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        if api_key:
            target_model = "gemini-embedding-2-preview" if "gemini-embedding-2" in model else model
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:embedContent?key={api_key}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                try:
                    resp = await client.post(url, json=body, headers={"Content-Type": "application/json"})
                    if resp.status_code == 200:
                        data = resp.json()
                        values = data.get("embedding", {}).get("values")
                        if values:
                            return values
                    else:
                        print(f"⚠️ Google AI Studio :embedContent ({resp.status_code}): {resp.text[:250]}", flush=True)
                except Exception as req_err:
                    print(f"⚠️ Google AI Studio :embedContent error: {req_err}", flush=True)
        return None

    # Mode 2: Vertex AI (Service Account / ADC)
    token = get_vertex_access_token()
    if not token:
        return None

    project = settings.gemini_project
    configured_loc = location or getattr(settings, "gemini_embedding_location", "us-central1") or "us-central1"

    # Match location to model ID:
    # On Vertex AI:
    #   us-central1 hosts "gemini-embedding-2-preview"
    #   global hosts "gemini-embedding-2"
    targets: list[tuple[str, str]] = []
    if "gemini-embedding-2" in model:
        if configured_loc == "us-central1":
            targets.append(("us-central1", "gemini-embedding-2-preview"))
            targets.append(("global", "gemini-embedding-2"))
        else:
            targets.append((configured_loc, "gemini-embedding-2"))
            targets.append(("us-central1", "gemini-embedding-2-preview"))
    else:
        targets.append((configured_loc, model))
        if configured_loc != "global":
            targets.append(("global", model))

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        for current_loc, target_model in targets:
            base_url = "https://aiplatform.googleapis.com" if current_loc == "global" else f"https://{current_loc}-aiplatform.googleapis.com"
            url = f"{base_url}/v1/projects/{project}/locations/{current_loc}/publishers/google/models/{target_model}:embedContent"
            try:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    values = data.get("embedding", {}).get("values")
                    if values:
                        return values
                else:
                    print(f"⚠️ Vertex :embedContent ({current_loc}, {target_model}, status {resp.status_code}): {resp.text[:250]}", flush=True)
            except Exception as req_err:
                print(f"⚠️ Vertex :embedContent network error on ({current_loc}, {target_model}): {req_err}", flush=True)

    return None



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
