"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Which engine transcribes pages: azure (Document Intelligence OCR) or groq (vision LLM).
    transcribe_provider: Literal["azure", "groq"] = "azure"

    groq_api_key: SecretStr | None = None

    # Azure Document Intelligence
    azure_di_endpoint: str | None = None
    azure_di_key: SecretStr | None = None
    azure_di_model: str = "prebuilt-read"
    # F0 (free) allows 1 analyze request/second: keep 1. S0 allows 15/second.
    azure_max_concurrency: int = Field(default=1, ge=1)
    azure_image_max_px: int = Field(default=2400, ge=500, le=10000)  # OCR needs more detail
    azure_low_confidence: float = Field(default=0.3, ge=0, le=1)  # below: word flagged unclear

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
    # 429s get their own, larger budget: on a low-limit tier they are expected, not faults.
    vision_rate_limit_retries: int = Field(default=10, ge=0)
    # Client-side pacing; 0 disables. Groq free tier for qwen3.8-27b: 8,000 tokens/minute.
    vision_tokens_per_minute: int = Field(default=8000, ge=0)
    vision_backoff_base_s: float = Field(default=1.0, ge=0)
    vision_backoff_max_s: float = Field(default=30.0, ge=0)
    vision_max_tokens: int = Field(default=2048, ge=256, le=16384)

    # Grading (Groq text model). Free tier: 8,000 tokens/minute, 200,000/day for this model.
    grading_model: str = "openai/gpt-oss-120b"
    grading_reasoning_effort: Literal["low", "medium", "high"] = "medium"
    grading_response_format: Literal["json_schema", "json_object"] = "json_schema"
    grading_temperature: float | None = Field(default=0.2, ge=0, le=2)  # steadier marks
    grading_max_concurrency: int = Field(default=2, ge=1)
    grading_tokens_per_minute: int = Field(default=8000, ge=0)  # 0 disables pacing
    grading_max_tokens: int = Field(default=4096, ge=256)  # includes reasoning tokens

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

    # Web app (phase 3)
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    storage_dir: Path = Path("data/storage")  # uploaded PDFs and page images (student data)
    export_dir: Path = Path("data/exports")  # generated mark sheets
    # Optional college master workbook: each exam's sheet is added/replaced in it.
    excel_master_path: Path | None = None
    job_runner: Literal["inline", "arq"] = "inline"  # arq + Redis in deployment
    redis_url: str = "redis://localhost:6379"
    session_ttl_hours: int = Field(default=12, ge=1)
    cookie_secure: bool = False  # set true when served over HTTPS
    login_max_failures: int = Field(default=5, ge=1)
    login_lock_minutes: int = Field(default=15, ge=1)
    max_upload_mb: int = Field(default=50, ge=1)
    page_image_max_px: int = Field(default=1800, ge=500)  # page images for the review screen

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, url: str) -> str:
        """Accept the plain URL Supabase shows (postgresql://...) and use the async driver."""
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
