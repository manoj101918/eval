"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None

    # Models
    transcribe_model: str = "claude-haiku-4-5"
    grading_model: str = "claude-sonnet-5-5"

    # Vision calls
    vision_max_concurrency: int = Field(default=5, ge=1)
    vision_call_timeout_s: float = Field(default=90.0, gt=0)
    vision_max_retries: int = Field(default=4, ge=0)
    vision_backoff_base_s: float = Field(default=1.0, ge=0)
    vision_backoff_max_s: float = Field(default=30.0, ge=0)
    vision_max_tokens: int = Field(default=8000, ge=256)

    # Image preprocessing
    image_max_px: int = Field(default=1500, ge=100)
    jpeg_quality: int = Field(default=80, ge=1, le=95)
    pdf_render_dpi: int = Field(default=200, ge=72, le=600)
    blank_ink_ratio: float = Field(default=0.0001, ge=0, le=1)

    # Answer booklet layout
    has_cover_page: bool = True
    roll_number_pattern: str = r"[A-Z0-9]{5,15}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
