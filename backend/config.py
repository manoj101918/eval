"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: SecretStr | None = None

    # Vision model (Groq)
    vision_model: str = "qwen/qwen3.8-27b"
    vision_reasoning_effort: Literal["none", "default", "low", "medium", "high"] = "none"
    # json_object: schema given in the prompt (default; strict json_schema made qwen3.8-27b
    # return empty transcriptions with reasoning off). json_schema: constrained decoding.
    vision_response_format: Literal["json_schema", "json_object"] = "json_object"

    # Vision calls
    vision_max_concurrency: int = Field(default=2, ge=1)
    vision_call_timeout_s: float = Field(default=90.0, gt=0)
    vision_max_retries: int = Field(default=4, ge=0)
    vision_backoff_base_s: float = Field(default=1.0, ge=0)
    vision_backoff_max_s: float = Field(default=30.0, ge=0)
    vision_max_tokens: int = Field(default=4096, ge=256, le=16384)

    # Image preprocessing
    image_max_px: int = Field(default=1500, ge=100)
    jpeg_quality: int = Field(default=80, ge=1, le=95)
    pdf_render_dpi: int = Field(default=200, ge=72, le=600)
    blank_ink_ratio: float = Field(default=0.0001, ge=0, le=1)

    # auto: one vision check per script decides the rotation; or force 0/90/180/270
    # (degrees counter-clockwise) when the scanner always feeds pages the same way.
    page_rotation: Literal["auto", "0", "90", "180", "270"] = "auto"

    # Answer booklet layout
    has_cover_page: bool = True
    roll_number_pattern: str = r"[A-Z0-9]{5,15}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
