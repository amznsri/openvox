# Session log

Chronological record of what was built and why. The detailed engineering rationale
lives in `CLAUDE.md` (root); this file is a lightweight changelog you can grep by
feature or symptom.

> Format: each session block is dated and tagged. "Decisions" notes the *why*; "Bugs
> fixed" notes problems we won't repeat (also indexed in `CLAUDE.md` §8).

---

## Session 1 — Foundation rewrite (v2)

**Goal**: previous v1 (`../openvox/`) had a non-functional dashboard and used Volcano
Engine APIs instead of BytePlus. User asked for a full rewrite in `/openvox-v2/`,
local-first, full feature scope, Apache-2.0.

**Built**:
- Monorepo: `apps/dashboard` (Next.js 14), `packages/core` (Python FastAPI),
  `packages/server` (Node Fastify gateway), `packages/sdk-ts`, `packages/sdk-py`,
  `packages/cli`.
- Core voice pipeline (`pipeline/orchestrator.py`) — async, sentence-flush,
  interruption-aware.
- 14 providers wired through a registry: BytePlus (STT/TTS/LLM/RTC), OpenAI,
  Anthropic, Gemini, DeepSeek, ElevenLabs, Cartesia, OpenAI TTS, Deepgram,
  AssemblyAI, Whisper.
- 4 storage backends: local FS, BytePlus TOS, S3/MinIO, GCS/OSS scaffold.
- Dashboard with 9 pages (landing, overview, playground voice/text, agents
  list/new/detail, templates, providers, skills runner, observability, settings).
- 5 templates and ~12 skills.
- TS + Python SDKs, `openvox` CLI.

**Decisions**:
- Default LLM `seed-2-0-pro-260328` (not `doubao-seed-1.6-…`); voice
  `en_male_tim_uranus_bigtts` (TTS 2.0 only — `seed-tts-2.0` resource id).
- npm for server/dashboard (avoids pnpm symlink issues in Alpine).
- BytePlus TTS auto-derive of resource id was reverted — single resource id
  `seed-tts-2.0` per user direction.
- Local-first auth defaults to disabled.

**Bugs fixed during build-out**: every entry in `CLAUDE.md` §8 was discovered and
fixed in this session. Highlights: pnpm in Docker, multipart 415, TLS cert chains
through Zscaler, Fastify v5 WS API change, BytePlus STT 4-byte sequence parsing,
tool-call streaming fragments + history ordering for Ark.

---

## Session 2 — Audio file mode + Document Q&A

**Goal**: per user request, add audio-file upload to the Audio Analyzer template and
build a Document Q&A agent with PDF/image upload + voice in/out.

**Built**:
- `POST /api/v1/playground/audio_analyze` — multipart audio → transcript + sentiment +
  profanity. Streams via `pydub` decode → BytePlus STT WS.
- New DB tables `documents`, `document_chunks`.
- `openvox/rag/` module: `extract.py` (PDF/text/image), `embeddings.py` (Ark embeddings
  client), `store.py` (chunk + embed + cosine search), `bm25.py` (keyword fallback),
  `byteplus_cloud.py` (RAG Cloud client with HMAC-SHA256 SigV4 signer).
- New routes: `POST/GET/DELETE /api/v1/agents/{id}/documents`, `POST
  /api/v1/playground/document_query`.
- New skills: `query_documents` (RAG with cloud→local fallback), `analyze_image`
  (vision via Seed-2.0 multimodal).
- New template: `document-qa`.
- Dashboard: Documents tab on agent edit page (upload/list/delete with live indexing
  status), Documents tab in Playground (text Q&A + voice in/out).
- `LLMMessage.content` extended to `str | list[dict]` for vision content blocks.

**Decisions**:
- BM25 fallback (pure Python) rather than chromadb — keeps install lean and works
  out-of-the-box when Ark embeddings aren't activated.
- Documents stored in the configured storage backend (local FS by default); chunks
  stored in SQL with embeddings as JSON arrays. Brute-force NumPy cosine for vector
  search.
- For audio uploads: stream via WS rather than batch URL (works without TOS).
- BytePlus RAG Cloud preferred when AK/SK + collection are set; local fallback on
  any error.

**Bugs fixed**:
- Multipart 415 from Fastify (only JSON parser registered by default) →
  `addContentTypeParser` for `multipart/*`, `application/octet-stream`, `text/*`.
- Embeddings 404 from Ark international → BM25 fallback engaged automatically.
- Documents stuck on "indexing…" → soft-fail: store chunks even when embeddings fail,
  show `keyword-only` badge instead of `error`.
- Delete button: switched to global `mutate(swrKey)` with optimistic remove, plus
  per-row spinner + error display.
- BytePlus STT clean-close raised `ConnectionClosedOK` → caught and treated as normal
  end-of-stream.
- Tool-calling: streaming arg fragments accumulated by `index`, history ordering
  fixed (assistant-with-tool_calls before tool-replies with `tool_call_id`).
- Orchestrator structure broken by misindented helper functions →
  `_speak`/`interrupt` moved back inside `VoiceSession`, helpers placed strictly after
  the class.

**RAG Cloud specifics noted**:
- Endpoint: `https://api-knowledgebase.mlp.cn-hongkong.bytepluses.com`
- Auth: HMAC-SHA256 SigV4 with service=`air`, region=`cn-hongkong`. NOT a Bearer
  token. Signing scheme implemented in `rag/byteplus_cloud.py`.

---

## Session 3 — Document Q&A voice mode

**Goal**: extend Document Q&A to accept voice questions and respond with TTS.

**Built**:
- `POST /api/v1/playground/transcribe` — single-shot audio → transcript.
- `POST /api/v1/playground/synthesize` — `{text, voice_id?}` → `audio/pcm` body with
  `X-Sample-Rate` header.
- Documents tab in Playground: mic button (MediaRecorder webm/opus) → transcribe →
  auto-submit → answer text + auto-play TTS via existing `AudioPlaybackQueue`.
  Recording / Transcribing / Speaking indicators, click-to-mute on Speaking.

**Decisions**:
- HTTP-based (single-shot) flow rather than WS streaming for the Documents tab. The
  Voice tab still uses the full WS pipeline; Documents is a focused "ask my docs"
  interaction with simpler state machine.
- Reuse the existing `AudioPlaybackQueue` from `apps/dashboard/src/lib/voice/audio.ts`.

---

## Session 4 — CLAUDE.md memory file

**Goal**: capture context for future sessions before context window exhaustion.

**Built**: `CLAUDE.md` at repo root (auto-loaded by Claude Code), this `SESSION_LOG.md`.

---

## Session 5 — Scheduler + MCP + Receptionist template

**Goal**: ship the three items from `PLANNING_NEXT.md` in priority order.

**Built**:
- **Task scheduler** (`openvox/scheduler/`):
  - `engine.py` — `AsyncIOScheduler` wrapper. Persistent source-of-truth in
    our DB; APScheduler in-memory state rebuilt at startup.
  - `runner.py` — three job kinds: `agent_query`, `skill_run`, `audio_batch`.
    The audio_batch runner reuses `_decode_to_pcm16k` + `_stream_pcm_to_stt`
    from the playground route, walks a folder, tracks processed files in a
    `.openvox_processed` state file.
  - New tables: `ScheduledJob`, `JobRun`.
  - `/api/v1/jobs` CRUD + `/{id}/trigger` for manual run + `/{id}/runs` history.
  - Dashboard page `/dashboard/schedules` with create/edit modal, run-now,
    pause/enable, history pull-down, 5s auto-refresh polling.
- **MCP integration** (`openvox/mcp/`):
  - `bridge.py` — `MCPSessionManager` (spawns stdio subprocs or opens SSE),
    `_make_skill()` wraps each MCP tool as a `BaseSkill` with namespaced id
    `mcp__<server>__<tool>`.
  - `Agent.mcp_servers` JSON column (with additive-column migration in
    `init_db`).
  - WS voice route now connects to MCP servers on session start, passes
    bridged skills to `SkillRunner(extra_skills=...)`, tears down on disconnect.
  - `SkillRunner` extended to accept `extra_skills` (per-session, doesn't
    pollute the global registry).
  - `/api/v1/mcp/probe` for dashboard validation.
  - New "MCP" tab on the agent edit page with add/probe/remove UI.
- **Receptionist template + skills** (`openvox/skills/builtin/reception.py`):
  - 5 skills (`business_info`, `check_availability`, `book_appointment`,
    `cancel_appointment`, `list_appointments`).
  - In-memory demo calendar: Acme Salon & Spa, 9-6 Mon-Fri, hourly slots,
    a few pre-seeded clashes so the agent can demo "that slot's taken, how
    about…".
  - Template `receptionist` with a step-by-step booking workflow in the
    system prompt (greet → check_availability → confirm slot → collect
    name+phone → book_appointment → read back confirmation code).

**Decisions**:
- APScheduler + own DB tables (NOT APScheduler's SQLAlchemy jobstore — that
  one needs a sync session and we're async-first).
- MCP bridge uses the official `mcp` PyPI SDK (lazy-imported so the rest of
  the codebase doesn't pay for it).
- MCP tool ids are namespaced (`mcp__server__tool`) to prevent collisions
  when two servers expose the same tool name.
- Per-session MCP skills shadow global registry entries — useful if a remote
  server intentionally replaces a built-in (e.g. real GitHub `get_repo`
  vs. our stub).
- DB schema bumps now go through a tiny `_ADDITIVE_COLUMNS` list in
  `init_db()` rather than a full Alembic migration system. Will switch to
  Alembic once schema changes slow down.

**Bugs fixed**:
- FK violation on `DELETE /api/v1/jobs/{id}` — `job_runs.job_id` FK blocked
  the parent delete. Fix: in-route cascade (DELETE job_runs first).
- `Agent.mcp_servers` column missing on existing DBs — `create_all` doesn't
  add columns. Fix: `init_db()` now ALTER TABLE ADD COLUMN IF NOT EXISTS.

**Verified end-to-end**:
- Scheduler: created interval job, manual trigger, read run history (1 success),
  delete returned 204.
- MCP: probe endpoint returns clean response on misconfigured commands (count=0).
- Receptionist: instantiated template, called `check_availability` → got 3 slots,
  called `book_appointment` → got confirmation code `APT-BEA722E5`.

**Open follow-ups** added back to `PLANNING_NEXT.md`:
- Outbound lead qualifier (SDR) template + Twilio dial-out path.
- Multilingual customer-support IVR template + `detect_language` skill.
- Scheduler webhook trigger (event-driven jobs).
- Skill hot-reload.

---

## Session 6 — SDR + Multilingual IVR templates

**Goal**: ship the remaining two templates from PLANNING_NEXT.md.

**Built**:
- **Outbound SDR** — `openvox/telephony/twilio.py` (REST client + `place_call`),
  `openvox/skills/builtin/sales.py` (4 skills: `fetch_next_lead`,
  `record_disposition`, `qualified_leads`, `book_demo`), template `sales-sdr`
  with BANT system prompt, new scheduler kind `outbound_call_batch`,
  `/api/v1/telephony/twilio/place_call` REST endpoint.
- **Multilingual IVR** — `openvox/skills/builtin/language.py` (`detect_language`
  skill exposes the last STT result's language), `Agent.voice_map` JSON column
  (additive migration), orchestrator's `_speak()` picks the voice for the
  currently detected language. Template `multilingual-support` with FAQ
  doc Q&A across 51 languages.

**Decisions**:
- Twilio outbound uses the REST API directly (POST /Calls.json) — small, no
  extra dep beyond what `twilio>=9.3.0` already gives us.
- BANT scoring is in-memory; real installs swap `sales.py` for a CRM-backed
  module or wire a CRM MCP server (HubSpot, Salesforce).
- Voice-map lookup falls back to agent's default `voice_id` if no entry for
  the detected language — keeps the demo working even with one voice activated.
- `detect_language` is **dual-path**: returns the ASR language if the
  orchestrator stashed one on ctx.metadata; falls back to LLM
  classification on the supplied text. Streaming ASR mode
  (`bigmodel_async`) doesn't support `enable_auto_lang` server-side, so
  the LLM fallback is the reliable path.

**Bugs / friction encountered**:
- Shell `curl … | python3 -c "..."` chokes when the response body contains
  literal newlines (e.g. `system_prompt` of the SDR template). API works
  fine; only the smoke-test pipeline fails. Use `python -m json.tool` on
  output written to a file, or pipe through `jq -R -s 'fromjson'`.

**Verified end-to-end**:
- 8 templates total (`/api/v1/templates`), 26 skills registered.
- SDR: `fetch_next_lead` → `LEAD-001` (Northwind Logistics);
  `record_disposition(80/85/90/75)` → score=82, bucket=`qualified`,
  next_step=`book_demo`, DISP-22724423 persisted.
- Multilingual: `detect_language("Hola, necesito ayuda con mi factura")`
  → `es-ES`, method=`llm`. `route_to_specialist(billing, es-MX)` →
  `billing-es`, agent Carlos, 4 min wait.
- Both agents instantiate correctly with template defaults: Mira SDR
  (7 skills, voice_map=0), Polyglot Support (4 skills, voice_map=7 entries).

---

## Open follow-ups (carried forward)

Updated end of Session 6. Items shipped this session removed; items still
pending below.

1. **Scheduler webhook trigger** (event-driven jobs).
2. **Skill hot-reload** (`watchfiles` on `~/.openvox/skills/`).
3. **Curated MCP server catalogue** with one-click pre-fill.
4. **CRM-via-MCP** for the SDR template (HubSpot / Salesforce snippets).
5. **VAD provider**: Silero VAD locally, BytePlus VAD when launched.
6. **Speech-to-Speech**: OpenAI Realtime adapter (BytePlus S2S not yet GA).
7. **Live interpretation**: simultaneous translation pipeline.
8. **Voice podcast generation**.
9. **BytePlus RTC client SDK** wiring (server-side token issuance done).
10. **Twilio Media Streams** ↔ pipeline bridge for the inbound path
    (outbound dial-out path lands in Session 6; inbound Media Stream
    handler in WS is still scaffolded).
7. **WhatsApp Business inbound** message routing (verify done).
8. **Telegram bot** message routing (webhook scaffolded).
9. **Alembic migrations** (currently using `Base.metadata.create_all()`).
10. **Test suite** — `packages/core/tests/` is empty.
11. **GCS, Alibaba OSS** storage implementations (interface defined).
12. **CLI**: `deploy`, `logs`, `dev` subcommands.
13. **Cloud-hosted multi-tenant mode** + OAuth (scaffold present, disabled).
