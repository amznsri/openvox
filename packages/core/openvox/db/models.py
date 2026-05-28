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
    # VAD provider id (see openvox/providers/vad/). Default "silero" runs
    # the local ONNX detector for sub-100 ms interrupt latency. Set "none"
    # to fall back to the older client-driven path (the dashboard mic
    # component sends `{type:"interrupt"}` itself).
    vad_provider: Mapped[str] = mapped_column(String(50), default="silero")

    # Speech-to-Speech provider id (Phase 3 PR-B, v0.2.24). When set,
    # the orchestrator opens an S2S session against this provider INSTEAD
    # of running the STT→LLM→TTS pipeline — single-WS-hop voice for
    # ~120 ms first-byte latency at the cost of provider portability.
    # Empty string is the canonical "pipeline mode" sentinel; populated
    # values: "openai_realtime" (today; Gemini Live planned). The
    # stt/tts/llm fields are still consulted as FALLBACK when the S2S
    # provider isn't available (no key, network issue) — see
    # `s2s_bridge.py` for the failover logic.
    s2s_provider: Mapped[str] = mapped_column(String(50), default="")

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
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )

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

    # Telemetry for the pricing calculator. We track total LLM tokens
    # in/out and total TTS characters for the whole session — the cost
    # calculator turns these into $/component via the rate card.
    llm_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tts_chars: Mapped[int] = mapped_column(Integer, default=0)
    # ASR/STT input character count. Per-character STT providers
    # (BytePlus Seed ASR, Aliyun, Tencent) bill on this — without it
    # the pricing calculator has to proxy from tts_chars, which is
    # only accurate when the user and agent speak roughly equal amounts.
    stt_chars: Mapped[int] = mapped_column(Integer, default=0)

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
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )

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
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
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
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # D9-v2 (v0.2.21): promoted from soft string to hard FK with
    # CASCADE. The chunk-by-document cascade above already covers
    # the "delete agent" path transitively, but the direct FK is
    # cheap belt-and-braces — defends against any future code path
    # that inserts a chunk without a corresponding Document row
    # (shouldn't happen, but the constraint costs nothing).
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
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

    # Soft FK on purpose: empty agent_id is a valid value for
    # `audio_batch` kind jobs (which watch a folder, not an agent).
    # Promoting to a hard FK with nullable=False would reject those
    # inserts; promoting with nullable=True + SET NULL is workable
    # but ripples into the dashboard / SDK / scheduler-runner code
    # which currently treats this as `str`. D9-v2 deliberately
    # leaves this for a follow-up planning decision (audit-trail
    # semantics vs cascade-everything). The delete_agent route
    # still manually deletes scheduled_jobs scoped to the agent
    # being removed.
    agent_id: Mapped[str] = mapped_column(String(36), default="")

    # Trigger.
    # `webhook` joins the time-based triggers — these jobs fire only on
    # explicit POST to /api/v1/jobs/webhook/{token}. trigger_expr is
    # unused for webhook jobs; webhook_token holds the random URL slug.
    trigger_type: Mapped[str] = mapped_column(String(20), default="cron")  # cron|interval|once|webhook
    trigger_expr: Mapped[str] = mapped_column(String(200), default="0 20 * * *")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    # Set on creation for webhook jobs (also any other job that wants
    # an external-trigger backdoor — non-webhook jobs simply ignore it).
    # URL-safe random; check `Authorization` or path on the fire route.
    webhook_token: Mapped[str] = mapped_column(String(64), default="")

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
    job_id: Mapped[str] = mapped_column(
        ForeignKey("scheduled_jobs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
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



# ──────────────────────────────────────────────────────────────────────
# Session 8: evaluation framework.
#
# Three new tables, one wedge:
#   - Recording — a snapshot of a real Session promoted into a reusable
#     test fixture. Stores transcript_json so we can replay user turns
#     against a *different* agent config without needing the original
#     audio file.
#   - Persona — a synthetic "user" agent (a prompt that makes the LLM
#     behave like an angry customer / confused elder / non-native
#     speaker etc.). Used to spar against your candidate agents.
#   - EvalRun — one execution of (recording | persona) × agent, with the
#     resulting transcript, a verdict (pass/fail), and the criteria
#     used. CI integration polls this table.
#
# No FK back to Session — Recordings live independently of the source
# session so deleting a session doesn't nuke your test fixtures.
# ──────────────────────────────────────────────────────────────────────


class Recording(Base):
    """A saved conversation snapshot promoted from a live Session."""

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), default="")
    source_session_id: Mapped[str] = mapped_column(String(36), default="")
    source_agent_id: Mapped[str] = mapped_column(String(36), default="")
    # Full transcript as a list of turns: [{role, text, skill_id?, ...}, ...]
    # This is what we replay against a new agent config — no audio
    # required since we feed each user turn as STT-final text.
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    # Optional original audio URL (TOS / S3 / local). Empty when the
    # recording was synthetic.
    audio_url: Mapped[str] = mapped_column(String(500), default="")
    # Free-form labels for filtering: ["billing", "angry", "smoke-test"].
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Persona(Base):
    """A synthetic-user agent for sparring against real agents.

    Behaviour is entirely driven by `system_prompt` — the persona acts
    as an LLM-powered fake user, asking questions of the candidate
    agent until either the criteria are met or the turn cap is hit.
    """

    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    # Loose categorisation for browsing: ["customer", "angry", "english"].
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # LLM provider + model to drive the persona. Empty → use the
    # workspace default.
    llm_provider: Mapped[str] = mapped_column(String(50), default="byteplus")
    llm_model: Mapped[str] = mapped_column(String(100), default="")
    # Optional: voice to use when this persona drives a *voice* eval.
    # For text-only evals (default) this is ignored.
    voice_id: Mapped[str] = mapped_column(String(100), default="")
    # Was this seeded by the system? Built-ins are read-only in the UI.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvalRun(Base):
    """One execution of an eval — recording-replay or persona-vs-agent."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # Exactly one of these is set, depending on the eval mode.
    recording_id: Mapped[str] = mapped_column(String(36), default="")
    persona_id: Mapped[str] = mapped_column(String(36), default="")
    # User-supplied criteria — plain-English questions the judge LLM
    # will answer pass/fail on (e.g. ["Did the agent collect the order
    # number?", "Did sentiment stay positive?"]).
    criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Full conversation that resulted from the run.
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    # Aggregate outcome — pass | partial | fail | error.
    verdict: Mapped[str] = mapped_column(String(20), default="")
    # Numeric score 0..1 (fraction of criteria that judged "pass").
    score: Mapped[float] = mapped_column(default=0.0)
    # Per-criterion breakdown: [{criterion, verdict, reasoning}, ...]
    judge_breakdown: Mapped[list] = mapped_column(JSON, default=list)
    # If the run errored before judging completed, the message lives here.
    error: Mapped[str] = mapped_column(Text, default="")
    # How many turns the run produced (cap is enforced in the runner).
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    # Timing — useful for the CI dashboard.
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderKey(Base):
    """Encrypted provider API key — populated by the Phase 3 setup wizard.

    Resolution order at runtime is env var → this table. That means
    operators running OpenVox via Docker with `.env` populated never
    touch this table; the wizard's audience is CLI-mode personal users
    who'd otherwise have to hand-edit `.env`.

    Values are encrypted with a per-machine symmetric key stored at
    ``~/.openvox/secret.key`` (0600). See ``openvox/secrets.py`` for
    encrypt/decrypt + the key-lifecycle code.

    Composite primary key on (provider, key_name) keeps things simple
    — a provider can have multiple named keys (e.g. byteplus has
    separate LLM and Voice keys) and we want UPSERT semantics, not
    duplicates.
    """

    __tablename__ = "provider_keys"

    # e.g. "byteplus", "openai", "anthropic", "elevenlabs", "twilio"
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    # e.g. "llm_api_key", "voice_api_key", "access_key", "secret_key"
    key_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Fernet token — base64-encoded ciphertext + nonce + MAC. Long
    # because Fernet adds ~80 bytes of overhead per value.
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    # Audit trail — when last set. Advances on UPSERT.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow,
    )


class OAuthToken(Base):
    """OAuth 2.0 tokens for third-party integrations (Google, etc.).

    Separate table from ``provider_keys`` because the lifecycle is
    different:
      - ProviderKey: long-lived static API key, set once via the wizard,
        no automatic refresh, ResolveOrder = env → store.
      - OAuthToken: short-lived access token + long-lived refresh token,
        rotates on every use, NO env-var fallback (the user has to
        complete the OAuth browser dance once per integration).

    Composite primary key on (provider, user_email) so a single user
    can connect multiple Google accounts to OpenVox if they want
    (e.g. one for personal Gmail, one for work).

    Encrypted with the same Fernet key as ProviderKey — see
    ``openvox/secrets.py`` / ``openvox/oauth/store.py``.

    Phase 1 of PLANNING_SESSION18.md introduces this for the native
    Google OAuth flow that replaces the MCP-server-based Gmail /
    Calendar integration with a one-click "Connect Gmail" button.
    """

    __tablename__ = "oauth_tokens"

    # e.g. "google", "microsoft", "github". Lower-case, no whitespace.
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    # The signed-in user identity at the upstream provider. For Google
    # this is the email address ("alice@example.com"); for other IdPs
    # could be a username or sub claim. Composite key with provider
    # so the user can have multiple connected accounts.
    user_email: Mapped[str] = mapped_column(String(320), primary_key=True)

    # Fernet ciphertext blobs. Access token is short-lived (~1 hour
    # for Google); refresh token is long-lived (until revoked).
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)

    # Token endpoint to call when refresh is needed. Stored alongside
    # the tokens because each provider has its own URL.
    token_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    # The OAuth client_id that minted these tokens. Stored so the
    # refresh request can include it — required by Google.
    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # JSON array of granted scopes ("https://www.googleapis.com/auth/...").
    # Skills check `scopes` before invoking an operation that needs
    # one (e.g. send_email needs gmail.send).
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # When the access token expires. Use timezone-aware datetimes —
    # SQLite stores as TEXT, Postgres as TIMESTAMPTZ; SQLAlchemy
    # handles both via DateTime(timezone=True).
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow,
    )
