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

    # Rate limiting
    rate_limit_per_minute: int = 60

    class Config:
        env_file = ".env"
        case_sensitive = False
        populate_by_name = True


settings = Settings()