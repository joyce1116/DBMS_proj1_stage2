from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    mongo_uri: str = "mongodb://127.0.0.1:27018"
    mongo_db: str = Field(default="survey_phase1", pattern=r"^[a-zA-Z0-9_-]+$")
    mongo_timeout_ms: int = Field(default=3000, ge=100, le=60000)
    session_ttl_seconds: int = Field(default=86400, ge=60, le=2592000)
