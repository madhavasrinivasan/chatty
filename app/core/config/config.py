from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # App
    app_name: str = Field(default="chatty")
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

    # Google GenAI
    gemini_api_key: str

    # Shopify (for OAuth / token exchange)
    shopify_api_key: str = Field(default="")
    shopify_api_secret: str = Field(default="")
    shopify_api_version: str = Field(default="2024-01")

    class Config:
        env_file = ".env"
        case_sensitive = False
        populate_by_name = True
        extra = "ignore"  # Ignore extra fields like the typo 'secert_key'


settings = Settings()