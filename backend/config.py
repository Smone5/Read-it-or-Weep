"""Runtime configuration.

Everything comes from environment variables (a local ``.env`` file is loaded for
development), so the same container image runs unchanged on any cloud.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    # --- core ---------------------------------------------------------------
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    app_secret: str = field(default_factory=lambda: _env("APP_SECRET"))
    db_pool_max: int = field(default_factory=lambda: int(_env("DB_POOL_MAX", "20")))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8100").rstrip("/"))
    web_base_url: str = field(default_factory=lambda: _env("WEB_BASE_URL", "").rstrip("/"))
    cors_origins: list[str] = field(default_factory=lambda: _env_list("CORS_ORIGINS"))
    web_dist_dir: str = field(default_factory=lambda: _env("WEB_DIST_DIR", str(BACKEND_DIR.parent / "web" / "dist")))

    # First admin, created at start-up if no admin exists yet.
    bootstrap_admin_email: str = field(default_factory=lambda: _env("ADMIN_EMAIL"))
    bootstrap_admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))

    # --- file storage: local | s3 | azure | gcs --------------------------------
    storage_backend: str = field(default_factory=lambda: _env("STORAGE_BACKEND", "local").lower())
    storage_local_dir: str = field(default_factory=lambda: _env("STORAGE_LOCAL_DIR", "var/storage"))
    storage_bucket: str = field(default_factory=lambda: _env("STORAGE_BUCKET"))
    storage_prefix: str = field(default_factory=lambda: _env("STORAGE_PREFIX").strip("/"))
    s3_endpoint_url: str = field(default_factory=lambda: _env("S3_ENDPOINT_URL"))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION"))
    azure_connection_string: str = field(default_factory=lambda: _env("AZURE_STORAGE_CONNECTION_STRING"))
    azure_account_url: str = field(default_factory=lambda: _env("AZURE_STORAGE_ACCOUNT_URL"))

    # --- AI provider: openai | azure -------------------------------------------
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai").lower())
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL"))
    azure_openai_endpoint: str = field(default_factory=lambda: _env("AZURE_OPENAI_ENDPOINT"))
    azure_openai_api_key: str = field(default_factory=lambda: _env("AZURE_OPENAI_API_KEY"))
    azure_openai_api_version: str = field(default_factory=lambda: _env("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"))

    # Model names (on Azure these are your deployment names).
    model_writer: str = field(default_factory=lambda: _env("MODEL_WRITER", "gpt-6.2-sol"))
    model_checker: str = field(default_factory=lambda: _env("MODEL_CHECKER", "gpt-6.2-sol"))
    model_vision: str = field(default_factory=lambda: _env("MODEL_VISION", "gpt-6.2-sol"))
    model_fast: str = field(default_factory=lambda: _env("MODEL_FAST", "gpt-6-luna"))
    model_embedding: str = field(default_factory=lambda: _env("MODEL_EMBEDDING", "text-embedding-3-small"))
    model_voice: str = field(default_factory=lambda: _env("MODEL_VOICE", "gpt-realtime-2.1-mini"))
    model_voice_premium: str = field(default_factory=lambda: _env("MODEL_VOICE_PREMIUM", "gpt-realtime-2.1"))
    # With an ElevenLabs voice the host's lines are written by a text model and performed by ElevenLabs
    # (services/eleven.py). These are for that path.
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_engine: str = field(default_factory=lambda: _env("ELEVENLABS_ENGINE", "socket"))   # socket | dialogue | tts
    elevenlabs_model: str = field(default_factory=lambda: _env("ELEVENLABS_MODEL"))               # empty: the engine's own
    # Lines sent to ElevenLabs at the same moment, across every game: the plan's concurrency limit (more queue briefly).
    elevenlabs_concurrency: int = field(default_factory=lambda: int(_env("ELEVENLABS_CONCURRENCY", "3") or 3))
    model_host_writer: str = field(default_factory=lambda: _env("MODEL_HOST_WRITER", _env("MODEL_FAST", "gpt-6-luna")))
    host_writer_effort: str = field(default_factory=lambda: _env("HOST_WRITER_EFFORT", "none"))
    model_transcribe: str = field(default_factory=lambda: _env("MODEL_TRANSCRIBE", "gpt-4o-mini-transcribe"))
    model_speech: str = field(default_factory=lambda: _env("MODEL_SPEECH", "gpt-4o-mini-tts"))     # the backup voice
    llm_concurrency: int = field(default_factory=lambda: int(_env("LLM_CONCURRENCY", "6")))
    prefill_music: bool = field(default_factory=lambda: _env("PREFILL_MUSIC", "true").lower() in ("1", "true", "yes"))

    # --- Apple Music (optional) ----------------------------------------------
    apple_team_id: str = field(default_factory=lambda: _env("APPLE_TEAM_ID"))
    apple_key_id: str = field(default_factory=lambda: _env("APPLE_MUSIC_KEY_ID"))
    apple_private_key_path: str = field(default_factory=lambda: _env("APPLE_MUSIC_PRIVATE_KEY_PATH"))
    apple_private_key_b64: str = field(default_factory=lambda: _env("APPLE_PRIVATE_KEY_B64"))

    def validate(self) -> None:
        missing = [name for name, value in (("DATABASE_URL", self.database_url), ("APP_SECRET", self.app_secret)) if not value]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        if len(self.app_secret) < 32:
            raise RuntimeError("APP_SECRET must be at least 32 characters.")
        if self.app_secret.lower().startswith("change-me"):
            raise RuntimeError("APP_SECRET is still the example value. Generate one: openssl rand -base64 48")

    @property
    def ai_configured(self) -> bool:
        if self.llm_provider == "azure":
            return bool(self.azure_openai_endpoint and self.azure_openai_api_key)
        return bool(self.openai_api_key)


config = Config()
