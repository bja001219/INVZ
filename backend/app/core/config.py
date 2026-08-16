from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GenerationMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    generation_mode: GenerationMode = GenerationMode.MOCK
    openai_api_key: SecretStr = SecretStr("")
    kie_api_key: SecretStr = SecretStr("")
    database_url: str = "sqlite+aiosqlite:///./data/app-mock.db"
    frontend_origin: str = "http://localhost:5173"
    generation_max_attempts: int = Field(default=3, ge=1, le=10)
    generation_concurrency: int = Field(default=3, ge=1, le=16)
    retry_base_delay_sec: float = Field(default=1, gt=0)
    provider_poll_interval_sec: float = Field(default=1, gt=0)
    generation_attempt_timeout_sec: float = Field(default=120, gt=0)
    webhook_secret: SecretStr = SecretStr("")
    webhook_public_url: str = ""
    self_base_url: str = "http://127.0.0.1:8000"
    mock_webhook_delay_sec: float = Field(default=1, ge=0)

    @model_validator(mode="after")
    def live_requires_keys(self) -> "Settings":
        if self.generation_mode is GenerationMode.LIVE:
            openai_key = self.openai_api_key.get_secret_value()
            kie_key = self.kie_api_key.get_secret_value()
            if not openai_key.strip() or not kie_key.strip():
                raise ValueError("live mode requires OPENAI_API_KEY and KIE_API_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
