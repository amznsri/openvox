"""Centralised settings — read from env vars / .env, validated by pydantic.

Local-first: SQLite + filesystem storage are the defaults so the platform
runs out of the box with zero credentials. Configure real providers by
setting the corresponding env vars.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """Resolve the canonical OpenVox data directory.

    Always absolute: `~/.openvox` on every machine. The old default
    was `./.openvox` (CWD-relative), which combined with the daemon's
    `WorkingDirectory=~/.openvox/` setting produced the surprising
    nested path `~/.openvox/.openvox/openvox.db`. Worse, running
    `openvox run` from any other directory created a fresh empty
    DB at that directory's `./.openvox/openvox.db`, so users running
    the daemon AND the foreground command would see different data.

    Override via the `OPENVOX_DATA_DIR` env var if you want to put
    it elsewhere — fully-qualified absolute paths only.
    """
    return Path.home() / ".openvox"


def _default_database_url() -> str:
    """SQLite URL pinned to an absolute path under the data dir.

    `sqlite+aiosqlite:///<absolute-path>` — note the THREE slashes
    after the scheme (RFC 3986 file-URI form). The `/path/to/file`
    after them is the absolute filesystem path.
    """
    return f"sqlite+aiosqlite:///{_default_data_dir()}/openvox.db"


class Settings(BaseSettings):
    """Application settings.

    Field names map to env vars in upper-case (pydantic-settings default).
    Every field has a sensible default for local-only operation.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────
    node_env: Literal["development", "production", "test"] = "development"
    log_level: str = "info"
    core_port: int = 8000
    server_port: int = 3001
    dashboard_port: int = 3000
    data_dir: Path = Field(default_factory=_default_data_dir)

    # ── Auth (off by default — local-first) ───────────────────────────
    openvox_auth: Literal["disabled", "enabled"] = "disabled"
    jwt_secret: str = "change-me-locally"
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # ── Database / cache ──────────────────────────────────────────────
    # default_factory because Path.home() can't be evaluated at module
    # import time on some systems (e.g. when HOME is unset in CI). Also
    # — using a factory means the value computes ONCE per Settings()
    # instance, not at every attribute access.
    database_url: str = Field(default_factory=_default_database_url)
    redis_url: str = "redis://localhost:6379"

    # ── BytePlus (default provider stack) ─────────────────────────────
    byteplus_llm_api_key: str = ""
    byteplus_llm_endpoint_id: str = ""
    byteplus_llm_model: str = "seed-2-0-pro-260328"
    # Accept both `ap-southeast` (Ark host suffix) and `ap-southeast-1`
    # (the AWS-style naming we use elsewhere for TOS). `cn-beijing` routes
    # to the China deployment.
    byteplus_llm_region: str = "ap-southeast"
    # Embeddings (used by the Document Q&A agent for vector retrieval).
    # Common choices: doubao-embedding-large-text-240915, doubao-embedding-text-240715
    byteplus_embedding_model: str = "doubao-embedding-large-text-240915"
    # BytePlus RAG Cloud (managed knowledge base). Authenticates with an
    # AK/SK pair via HMAC-SHA256 request signing — NOT a Bearer token.
    # When both AK/SK and a collection name are present, the doc agent
    # routes queries to RAG Cloud instead of the local vector store.
    # Docs: https://docs.byteplus.com/en/docs/RAG_Cloud/Signature_authentication_and_examples
    byteplus_rag_access_key: str = ""
    byteplus_rag_secret_key: str = ""
    byteplus_rag_collection: str = ""
    byteplus_rag_endpoint: str = "https://api-knowledgebase.mlp.cn-hongkong.bytepluses.com"
    byteplus_rag_region: str = "cn-hongkong"
    byteplus_rag_service: str = "air"
    byteplus_voice_api_key: str = ""
    # OpenVox targets Seed-Speech 2.0 only (resource id `seed-tts-2.0`).
    # The default voice must be one your BytePlus key has activated in
    # the console — see https://docs.byteplus.com/en/docs/byteplusvoice/voicelist
    byteplus_tts_default_voice: str = "en_male_tim_uranus_bigtts"
    byteplus_rtc_app_id: str = ""
    byteplus_rtc_app_key: str = ""

    # ── Other LLMs ────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    deepseek_api_key: str = ""

    # ── Other STT / TTS ───────────────────────────────────────────────
    deepgram_api_key: str = ""
    assemblyai_api_key: str = ""
    whisper_mode: Literal["api", "local"] = "api"
    elevenlabs_api_key: str = ""
    elevenlabs_default_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    cartesia_api_key: str = ""
    cartesia_default_voice_id: str = ""
    openai_tts_default_voice: str = "alloy"

    # ── Telephony ─────────────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # ── Storage ───────────────────────────────────────────────────────
    storage_backend: Literal["local", "byteplus_tos", "s3", "gcs", "alibaba_oss"] = "local"
    storage_local_path: Path = Path("./.openvox/storage")
    byteplus_tos_access_key: str = ""
    byteplus_tos_secret_key: str = ""
    byteplus_tos_endpoint: str = "tos-ap-southeast-1.bytepluses.com"
    byteplus_tos_region: str = "ap-southeast-1"
    byteplus_tos_bucket: str = "openvox-media"
    s3_bucket: str = "openvox-media"
    s3_region: str = "us-east-1"
    s3_endpoint: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    gcs_bucket: str = ""
    gcs_credentials_json: str = ""
    alibaba_oss_bucket: str = ""
    alibaba_oss_endpoint: str = ""
    alibaba_oss_access_key_id: str = ""
    alibaba_oss_access_key_secret: str = ""

    # ── Routing & policy ──────────────────────────────────────────────
    routing_strategy: Literal["latency", "cost", "quality", "round_robin", "sticky"] = "latency"
    routing_fallback_enabled: bool = True
    latency_budget_ms: int = 300

    # ── Privacy / GDPR ────────────────────────────────────────────────
    data_retention_days: int = 30
    enable_transcript_storage: bool = True
    enable_audio_storage: bool = False
    pii_masking_enabled: bool = True
    data_residency_region: str = "ap-southeast-1"

    # ── Observability ─────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = ""
    sentry_dsn: str = ""

    # ── Computed helpers ──────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.node_env == "production"

    @property
    def auth_enabled(self) -> bool:
        return self.openvox_auth == "enabled"

    @property
    def byteplus_llm_endpoint(self) -> str:
        """The chat-completions URL for BytePlus Ark.

        BytePlus Ark exposes an OpenAI-compatible endpoint. Region selects
        the international vs china deployment. We accept several common
        spellings so users don't have to remember the host suffix.
        """
        r = (self.byteplus_llm_region or "").lower()
        if r.startswith("cn"):
            return "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        return "https://ark.ap-southeast.bytepluses.com/api/v3/chat/completions"

    @property
    def byteplus_voice_base(self) -> str:
        return "voice.ap-southeast-1.bytepluses.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
