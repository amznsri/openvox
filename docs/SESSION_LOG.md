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

## Open follow-ups (carried forward)

These came up during sessions but weren't shipped — pick up in a future session:

1. **VAD provider**: Silero VAD locally, BytePlus VAD when launched.
2. **Speech-to-Speech**: OpenAI Realtime adapter (BytePlus S2S not yet GA).
3. **Live interpretation**: simultaneous translation pipeline.
4. **Voice podcast generation**.
5. **BytePlus RTC client SDK** wiring (server-side token issuance done).
6. **Twilio Media Streams** ↔ pipeline bridge (webhook scaffolded).
7. **WhatsApp Business inbound** message routing (verify done).
8. **Telegram bot** message routing (webhook scaffolded).
9. **Alembic migrations** (currently using `Base.metadata.create_all()`).
10. **Test suite** — `packages/core/tests/` is empty.
11. **GCS, Alibaba OSS** storage implementations (interface defined).
12. **CLI**: `deploy`, `logs`, `dev` subcommands.
13. **Cloud-hosted multi-tenant mode** + OAuth (scaffold present, disabled).
