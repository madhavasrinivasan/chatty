from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # App
    app_name: str = Field(default="symma")
    env: str = Field(default="development")
    debug: bool = Field(default=False)

    # Database
    database_url: str = Field(alias="db_url")

    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # API Key
    api_key_header: str = "x-api-key"

    # File Upload
    file_upload_directory_pdf: str = Field(default="Assets/PDF")
    file_upload_max_size: int = Field(default=1024 * 1024 * 5) # 5MB

    # AES
    secret_key: str = Field(default="")

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Working Directory
    lightrag_kv_storage: str = Field(default="PGKVStorage")
    lightrag_doc_status_storage: str = Field(default="PGDocStatusStorage")
    lightrag_graph_storage: str = Field(default="PGGraphStorage")
    lightrag_vector_storage: str = Field(default="PGVectorStorage")

    # Google GenAI — API key OR Vertex AI via ~/.config/gcloud/application_default_credentials.json
    gemini_api_key: str = Field(default="")
    gemini_use_vertexai: bool = Field(default=False)
    gemini_project: str = Field(default="")
    gemini_location: str = Field(default="us-central1")
    # Per-request hard timeout (ms) so a hung Gemini call can't stall the whole request.
    # Thinking is OFF (budget 0) so calls are fast; raise this if you enable thinking.
    gemini_timeout_ms: int = Field(default=12000)
    # Automatic retry attempts on transient errors (429/5xx). 2 => 1 initial + up to 1 retry.
    gemini_max_retries: int = Field(default=2)
    # Models per stage. flash = higher quality; flash-lite = faster but lower quality.
    gemini_router_model: str = Field(default="gemini-2.5-flash")
    gemini_synthesis_model: str = Field(default="gemini-2.5-flash")
    # Thinking budget per stage: 0 = thinking OFF (fastest), -1 = dynamic thinking
    # (model decides, slowest but highest quality), N = fixed token budget.
    # Both default OFF for lowest latency; bump synthesis to -1 or N for richer answers.
    gemini_router_thinking_budget: int = Field(default=0)
    gemini_synthesis_thinking_budget: int = Field(default=0)

    # Shopify (for OAuth / token exchange)
    shopify_api_key: str = Field(default="")
    shopify_api_secret: str = Field(default="")
    shopify_api_version: str = Field(default="2024-01")
    shopify_callback_domain: str = Field(default="")  # e.g. https://your-app.com
    comez_base_url: str = Field(default="http://localhost:3005")


    class Config:
        env_file = ".env"
        case_sensitive = False
        populate_by_name = True
        extra = "ignore"  # Ignore extra fields like the typo 'secert_key'


settings = Settings()