"""ORM models — agents, sessions, transcripts, skills."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openvox.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Agent(Base):
    """A configurable voice agent — bundle of name, providers, prompt, skills."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Provider stack
    stt_provider: Mapped[str] = mapped_column(String(50), default="byteplus")
    tts_provider: Mapped[str] = mapped_column(String(50), default="byteplus")
    llm_provider: Mapped[str] = mapped_column(String(50), default="byteplus")
    # Empty string → resolve to settings.byteplus_llm_model at agent
    # creation time. Existing records that already store an explicit model
    # name keep that value (edit them via the UI to refresh).
    llm_model: Mapped[str] = mapped_column(String(100), default="")

    # Voice characteristics
    voice_id: Mapped[str] = mapped_column(String(100), default="")
    voice_speed: Mapped[float] = mapped_column(default=1.0)
    voice_language: Mapped[str] = mapped_column(String(20), default="en-US")

    # Behaviour
    system_prompt: Mapped[str] = mapped_column(Text, default="You are a helpful voice assistant.")
    greeting: Mapped[str] = mapped_column(Text, default="Hi, how can I help you?")
    temperature: Mapped[float] = mapped_column(default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)

    # Skills (list of skill IDs / package names) — stored as JSON
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Channel config (telephony / whatsapp / web — JSON blob)
    channels: Mapped[dict] = mapped_column(JSON, default=dict)
    # MCP server configs — each entry shape:
    #   {name, transport: "stdio"|"sse", command, args, env, url}
    # Tools exposed by these servers are auto-bridged into the agent's tool
    # set at session start. See openvox/mcp/.
    mcp_servers: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Per-language TTS voice override — used by the multilingual IVR template.
    # Keys are BCP-47 short codes (`en`, `zh`, `es`, …). Orchestrator's
    # `_speak()` swaps voice to match the STT-detected language; empty
    # map means "always use voice_id".
    voice_map: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default=AgentStatus.DRAFT.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    sessions: Mapped[list["Session"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class Session(Base):
    """A conversation session — one user-agent interaction."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)

    # Channel: web | phone | whatsapp | telegram | api
    channel: Mapped[str] = mapped_column(String(20), default="web")
    caller_id: Mapped[str] = mapped_column(String(200), default="")

    # Counters / metadata
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)

    # Latency stats
    first_token_ms: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Storage references
    audio_url: Mapped[str] = mapped_column(String(500), default="")
    transcript_url: Mapped[str] = mapped_column(String(500), default="")

    # Final outcome
    status: Mapped[str] = mapped_column(String(20), default="active")
    error: Mapped[str] = mapped_column(Text, default="")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="sessions")
    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Transcript(Base):
    """A single utterance within a session — user or assistant."""

    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)

    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system | tool
    text: Mapped[str] = mapped_column(Text)
    audio_url: Mapped[str] = mapped_column(String(500), default="")

    # When token-streaming, we record start/end of audio for re-playback alignment.
    started_ms: Mapped[int] = mapped_column(Integer, default=0)
    ended_ms: Mapped[int] = mapped_column(Integer, default=0)

    # If this turn invoked a skill, capture the call.
    skill_id: Mapped[str] = mapped_column(String(100), default="")
    skill_args: Mapped[dict] = mapped_column(JSON, default=dict)
    skill_result: Mapped[dict] = mapped_column(JSON, default=dict)

    sentiment: Mapped[str] = mapped_column(String(20), default="")
    pii_redacted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[Session] = relationship(back_populates="transcripts")


class SkillRecord(Base):
    """Installed skills — both built-in and user-loaded packages."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    source: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin | local | git | pip
    package: Mapped[str] = mapped_column(String(200), default="")
    config_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Document(Base):
    """A file attached to an agent's knowledge base.

    Text-bearing files (pdf, docx, txt, md) are extracted and chunked
    into `DocumentChunk` rows on upload. Image files are stored as-is and
    referenced by URL in the chunk's `text` (so vision-capable LLMs can
    answer about them).
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_url: Mapped[str] = mapped_column(String(500), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentChunk(Base):
    """A retrievable slice of a Document with a precomputed embedding."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int] = mapped_column(Integer, default=0)
    # `kind`: "text" → plain text content; "image" → text contains a URL/data URI.
    kind: Mapped[str] = mapped_column(String(10), default="text")
    text: Mapped[str] = mapped_column(Text)
    # JSON array of floats. Brute-force cosine search at query time is
    # fast enough for the scale we expect on a local-first install.
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ScheduledJob(Base):
    """A recurring or one-off task that fires on cron/interval/once triggers.

    Three job kinds today (see scheduler/runner.py):
      - `agent_query`  — send a fixed text prompt to an agent and store the answer
      - `skill_run`    — invoke a skill directly with JSON args
      - `audio_batch`  — run audio_analyze on every file in a watched folder

    Adding new kinds is purely additive — add a branch in runner.execute().
    """

    __tablename__ = "scheduled_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")

    # Job kind + payload (kind-specific JSON blob).
    kind: Mapped[str] = mapped_column(String(40), default="agent_query")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    # Optional agent the job is scoped to (FK kept loose — we store the
    # string id so deleting the agent doesn't cascade-delete the schedule).
    agent_id: Mapped[str] = mapped_column(String(36), default="")

    # Trigger.
    trigger_type: Mapped[str] = mapped_column(String(20), default="cron")  # cron|interval|once
    trigger_expr: Mapped[str] = mapped_column(String(200), default="0 20 * * *")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # State.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="")  # success|error|running
    last_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class JobRun(Base):
    """One execution of a ScheduledJob. Kept for history + debugging."""

    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("scheduled_jobs.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|success|error
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class User(Base):
    """User — only used when OPENVOX_AUTH=enabled."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    provider: Mapped[str] = mapped_column(String(20), default="local")  # local | github | google
    provider_id: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
