# OpenVox — Project Memory

> Auto-loaded by Claude Code. **Read this first** when picking up the project in a new
> session. It captures everything that wasn't obvious from the file tree alone:
> architecture, decisions, current status, gotchas, and bugs we've already fixed (so
> we don't fix them again).

---

## 1. What we're building

**OpenVox** is an open-source, local-first voice agent platform. Users (developers and non-technical) build, test, and deploy production-grade
voice agents with pluggable providers, a sleek dashboard, an SDK, a CLI, and a skills
extension framework.

- Apache-2.0, single-machine deployable via `docker compose up`.
- Default provider stack is **BytePlus** (Seed-2.0 LLM + Seed-Speech 2.0 TTS + Seed
  ASR 2.0 + RTC + TOS object storage). Other providers (OpenAI, Anthropic, Gemini,
  DeepSeek, ElevenLabs, Cartesia, Deepgram, AssemblyAI, Whisper, Twilio, WhatsApp) are
  available as adapters.
- Sub-300 ms first-audio latency target (sentence-level token streaming pipeline).
- "Local-first" = your audio/transcripts stay on your machine until you opt into cloud
  storage. SQLite + filesystem are the defaults; Postgres + TOS work too.

The corresponding "v1" project lives at `../openvox/` — keep it as reference, but
`openvox-v2/` is the active codebase.

## 2. Quickstart for a fresh session

```bash
cd /Users/bytedance/Documents/ByteDance/NewModelDemos/openvox-v2
docker compose up --build         # full stack build + start
# Open http://localhost:3000
```

Useful service URLs:
- Dashboard:  http://localhost:3000
- Gateway:    http://localhost:3001  (Node Fastify; proxies to core)
- Core API:   http://localhost:8000  (Python FastAPI; voice pipeline)
- Postgres:   :5432  (user `openvox` / pw `openvox` / db `openvox`)
- Redis:      :6379

Key debug shortcuts:
```bash
# Core logs (most issues are here)
docker compose logs core --tail=80 2>&1 | grep -iE "error|except|traceback" | tail -20

# Live tail
docker compose logs -f core

# Run Python inside core container (registry not auto-bootstrapped — call register_builtins())
docker compose exec -T core python3 -c "
from openvox.providers.bootstrap import register_builtins
from openvox.skills.registry import get_skill_registry
register_builtins(); get_skill_registry()
# … your test code
"

# Compile-check Python after edits
find packages/core/openvox -name '*.py' -exec python3 -m py_compile {} \;

# Migrate stale agent records via the API (after default changes)
curl -s http://localhost:3001/api/v1/agents | python3 -c "..."
```

---

## 3. Architecture

```
                  ┌─────────────────────┐
   browser ─────► │ Next.js dashboard   │  :3000
                  └─────────┬───────────┘
                            │ REST + WS (multipart, JSON, binary)
                  ┌─────────▼───────────┐
   SDKs / CLI ──► │ Node gateway        │  :3001
                  │ Fastify v5 + JWT    │
                  └─────────┬───────────┘
                            │ HTTP proxy + WS bridge
                  ┌─────────▼───────────┐
   phone ───────► │ Python core         │  :8000
   WhatsApp ────► │ FastAPI + asyncio   │
                  │ pipeline + skills   │
                  └──┬───────────────┬──┘
                     │               │
              ┌──────▼──┐     ┌──────▼─────┐
              │Postgres │     │ Providers  │
              │ Redis   │     │ Storage    │
              └─────────┘     └────────────┘
```

### Core voice pipeline (`packages/core/openvox/pipeline/orchestrator.py`)

One `VoiceSession` per call. Async generators all the way down:

1. **Audio in** — frames pushed via `push_audio()` into a bounded queue.
2. **STT** — `transcribe_stream()` yields partial + final results.
3. **LLM turn** — final user utterance appended to history, `chat_stream()` tokens
   accumulate; **sentence boundaries flush a partial buffer to TTS** so first-audio
   arrives within ~300 ms of first token. Tool-calling is fully supported (see §6).
4. **TTS** — sentence chunks → `synthesize_stream()` → audio frames yielded as
   `assistant_audio` events.
5. **Interruption** — if the user starts speaking, `interrupt()` flips an event;
   the in-flight TTS stream cancels and emits `interrupt`.
6. **Skills (tools)** — invoked by the LLM via OpenAI-compatible tool-calls. Skill
   results are appended as `tool` messages and the LLM is re-invoked.

Events flow as `TurnEvent(kind, text, audio, sample_rate, encoding, data)` where
kind ∈ `user_partial | user_final | assistant_token | assistant_audio | assistant_done | skill_call | skill_result | tts_error | error | interrupt`.

### WS protocol over `/ws/voice`

```
client → server (text)    {"type":"start", "agent_id": "...", "sample_rate": 16000, ...}
client → server (binary)  raw PCM s16le mono frames @ 16 kHz
client → server (text)    {"type":"end" | "interrupt"}

server → client (text)    {"type":"user_partial|user_final|assistant_token|assistant_done|skill_call|skill_result|tts_error|error|interrupt", ...}
server → client (binary)  TTS PCM frames (sample_rate from agent's voice config, default 24 kHz)
```

The Node gateway transparently bridges this WS to the core's WS.

### Single-shot HTTP endpoints (Documents tab voice flow)

- `POST /api/v1/playground/transcribe`  — multipart audio → `{transcript, duration_ms}`
- `POST /api/v1/playground/synthesize`  — `{text, voice_id?}` → `audio/pcm` (header `X-Sample-Rate`)
- `POST /api/v1/playground/text`        — text-only chat (streaming)
- `POST /api/v1/playground/audio_analyze` — audio file → transcript + sentiment + profanity
- `POST /api/v1/playground/document_query` — RAG against an agent's KB

---

## 4. Repo layout

```
openvox-v2/
├── README.md  CLAUDE.md  LICENSE  .env.example  docker-compose.yml
├── docker/
│   ├── extra-ca.pem               # Drop corporate root here (Zscaler etc.)
│   └── postgres/init.sql
├── apps/dashboard/                # Next.js 14 App Router, Tailwind, custom UI
│   ├── Dockerfile                 # Build context = MONOREPO ROOT
│   └── src/
│       ├── app/
│       │   ├── page.tsx           # Marketing landing
│       │   ├── layout.tsx
│       │   └── dashboard/         # /dashboard/{,playground,agents,templates,...}
│       ├── components/{ui,nav,playground}/
│       └── lib/{api.ts,utils.ts,voice/audio.ts}
├── packages/
│   ├── core/                      # Python 3.12, FastAPI, asyncio
│   │   ├── Dockerfile  pyproject.toml  README.md  main.py
│   │   ├── tests/                 # (empty for now)
│   │   └── openvox/
│   │       ├── __init__.py  config.py  cli.py
│   │       ├── api/{app.py, routes/, ws/}
│   │       ├── pipeline/orchestrator.py
│   │       ├── providers/{base.py, registry.py, bootstrap.py,
│   │       │              byteplus/, openai_compat/}
│   │       ├── skills/{base.py, registry.py, runner.py, builtin/}
│   │       ├── rag/{__init__.py, embeddings.py, extract.py, store.py,
│   │       │       bm25.py, byteplus_cloud.py}
│   │       ├── storage/{base.py, local.py, s3.py, byteplus_tos.py, factory.py}
│   │       ├── db/{models.py, session.py}
│   │       ├── telephony/         # placeholders for Twilio, WhatsApp, Telegram
│   │       └── utils/http.py      # certifi + corp-CA + insecure-TLS escape hatch
│   ├── server/                    # Node 20, Fastify v5, ESM, npm (NOT pnpm)
│   │   ├── Dockerfile  package.json
│   │   └── src/{index.ts, config.ts, routes/, ws/}
│   ├── sdk-ts/                    # @openvox/sdk
│   ├── sdk-py/                    # openvox-sdk
│   └── cli/                       # `openvox` CLI (Node)
├── templates/README.md            # Catalogue lives in core/api/routes/templates.py
└── docs/{architecture.md, extending.md}
```

Important file pointers:
- **Templates catalogue**: `packages/core/openvox/api/routes/templates.py` (5 templates)
- **Skill list**: `packages/core/openvox/skills/builtin/__init__.py` lists modules; each
  module exports `SKILLS = [...]`
- **Provider registry**: `bootstrap.py:register_builtins()` registers 14 built-ins
- **Settings**: `openvox/config.py` — single source of truth, env-driven

---

## 5. Coding conventions

- **Python**: type hints throughout, async everywhere, dataclasses for DTOs,
  `from __future__ import annotations`. Comments explain *why*, not *what*. Errors
  surfaced to UI when actionable; soft-fail with helpful hints when not.
- **TypeScript**: strict mode. Server uses ESM (`"type": "module"`) — top-level await,
  `.js` extensions on relative imports because of `module: NodeNext`. Dashboard pages
  are `"use client"` (avoids SSR issues with hooks).
- **Provider pattern**: each provider implements `STTProvider`/`TTSProvider`/
  `LLMProvider`/`RTCProvider` from `providers/base.py`. `is_available()` checks if
  credentials are configured; `chat_stream()`/`transcribe_stream()`/`synthesize_stream()`
  yield events asynchronously.
- **Skill pattern**: `BaseSkill` subclass with `id`, `display_name`, `description`,
  `parameters` (JSON schema), and async `run(args, ctx)`. The OpenAI tool-spec is
  derived automatically.
- **Local-first defaults**: every config value works out of the box (SQLite + local
  storage + no auth). Cloud creds enable richer features but are not required.
- **Comments style**: prefer concise, prose-like paragraphs explaining the *one*
  non-obvious thing about a function. Avoid restating types.

---

## 6. Provider integrations — gotchas worth remembering

### BytePlus Ark — Seed-2.0 LLM
- Endpoint (international): `https://ark.ap-southeast.bytepluses.com/api/v3/chat/completions`
- Endpoint (China):         `https://ark.cn-beijing.volces.com/api/v3/chat/completions`
- Auth: `Authorization: Bearer <BYTEPLUS_LLM_API_KEY>`
- **Default model: `seed-2-0-pro-260328`** (NOT `doubao-seed-1.6-250615` — that was wrong).
- OpenAI-compatible request/response. Multimodal `content: [{"type":"text"|"image_url"...}]`
  works (see `analyze_image` skill).
- Tool-calling: streaming sends fragments by `index`. **Must accumulate** —
  `_merge_tool_call_deltas()` / `_finalise_tool_calls()` in orchestrator.py.
- After running a tool, history must include the **assistant message containing the
  `tool_calls`** *before* the `tool` reply (with matching `tool_call_id`). Otherwise
  Ark returns `400 Bad Request`.

### BytePlus Seed ASR 2.0 (streaming WS)
- URL: `wss://voice.ap-southeast-1.bytepluses.com/api/v3/sauc/bigmodel_async`
- Resource ID: `volc.seedasr.sauc.duration`
- Headers: `X-Api-Key`, `X-Api-Resource-Id`, `X-Api-Connect-Id`
- **Binary protocol** — header(4 bytes) + optional sequence(4) + payload-size(4) + payload.
  Server response **always includes the 4-byte sequence** (flags bit 0 set in the default
  `0b0001`). My initial parser missed this and treated the sequence as the payload size —
  **don't repeat that bug**. See `_parse_response()` in `byteplus/stt.py`.
- Result lives at `payload_msg.result.text` and `payload_msg.result.utterances[].definite`,
  not at the top level. `definite: true` marks finalised utterances in dual-pass mode.
- Use `enable_nonstream: true` in the start config for accurate finals.
- After the last audio frame, server **closes cleanly with WS code 1000**. Catch
  `websockets.exceptions.ConnectionClosedOK` and treat as normal end-of-stream.

### BytePlus Seed ASR 2.0 (audio-file batch)
- URL: `https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/{submit,query}`
- Resource ID: `volc.seedasr.auc`
- Submit accepts a public URL only. We use streaming-via-WS + `pydub` decoding for
  uploaded files instead, so any storage backend works (no TOS dependency).

### BytePlus Seed-Speech 2.0 (TTS, unidirectional HTTP)
- URL: `https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional`
- Resource ID: **always `seed-tts-2.0`** (we do NOT support TTS 1.0 per user direction).
- Required header: `X-Api-App-Key: aGjiRDfUWi` — fixed service constant.
- Body: `{user.id, req_params: {text, speaker, audio_params: {format, sample_rate, speech_rate}}}`.
- **Voice activation matters**: error `code=55000000 message='resource ID is mismatched
  with speaker related resource'` means the speaker isn't activated on the user's
  BytePlus key. Catalog: https://docs.byteplus.com/en/docs/byteplusvoice/voicelist
- Default voice: **`en_male_tim_uranus_bigtts`** (this is what the current user has
  activated).
- Response: newline-delimited JSON, `{code:0, data:"<base64 PCM>"}` per chunk;
  `{code: 20000000}` is terminal "ok"; any other code is an error to raise.

### BytePlus RAG Cloud (managed knowledge base)
- Endpoint: `https://api-knowledgebase.mlp.cn-hongkong.bytepluses.com`
- Auth: **AK + SK with HMAC-SHA256 SigV4-style signing** (NOT Bearer).
  - Service: `air`
  - Region: `cn-hongkong`
- Implementation in `openvox/rag/byteplus_cloud.py`. Reuses the standard SigV4 flow
  (canonical request → string-to-sign → derived signing key → HMAC).
- The `query_documents` skill **prefers RAG Cloud** when AK/SK + collection are set,
  falls back to local store on any error.
- Endpoints we use: `POST /api/knowledge/collection/service_chat` (multi-turn Q&A),
  `POST /api/knowledge/collection/info`. Add more in `BytePlusRAGClient` as needed.

### BytePlus TOS (object storage)
- SDK: `tos.TosClientV2(ak, sk, endpoint, region)` — synchronous; we wrap in
  `asyncio.to_thread`. See `storage/byteplus_tos.py`.
- Endpoint: `tos-ap-southeast-1.bytepluses.com` (default). Region `ap-southeast-1`.
- Used for audio recordings, transcripts (when storage backend is `byteplus_tos`).

### BytePlus RTC
- npm package: `@volcengine/rtc` (shared SDK between BytePlus and Volcengine).
- Server-side: `/api/v1/rtc/token` issues an HMAC-SHA256-signed token built from
  AppID + AppKey + roomID + userID + privileges + expiry. See `byteplus/rtc.py`.
- Client SDK wiring is **pending** — currently the playground uses direct WS streaming
  for browser audio.

### BytePlus Embeddings (often unavailable)
- Endpoint: `https://ark.ap-southeast.bytepluses.com/api/v3/embeddings` returns 404
  for the user's current account/region/model (`doubao-embedding-large-text-240915`).
- We don't block on this. `rag/store.py` tries embeddings, on failure stores chunks with
  empty vectors and falls back to **BM25 keyword search** (`rag/bm25.py`).
- Document badge in the dashboard shows `keyword-only` (yellow) instead of `error` (red)
  for this case.

### Other LLMs
- **OpenAI / DeepSeek**: pure OpenAI-compat — see `_openai_base.py`.
- **Gemini**: uses Google's OpenAI-compat endpoint at
  `generativelanguage.googleapis.com/v1beta/openai/chat/completions`.
- **Anthropic**: Messages API — translates internally; system messages pulled out
  into a top-level `system` field.

### Other STT/TTS
- **Deepgram** (WS), **AssemblyAI** (WS), **Whisper** (HTTP file or local).
- **ElevenLabs** (HTTP MP3 stream), **Cartesia** (SSE PCM), **OpenAI TTS** (HTTP).

### Task scheduler — `openvox/scheduler/`
- `AsyncIOScheduler` (APScheduler) wraps three trigger types:
  `cron` ("0 20 * * *"), `interval` ("30s|5m|1h|1d"), `once` (ISO datetime).
- Source-of-truth is our `scheduled_jobs` table; APScheduler's in-memory state
  is rebuilt at startup from the table. Every CRUD on a job also calls
  `register_or_update()` / `unregister()` so the running scheduler stays in sync.
- Three job kinds — `agent_query` (LLM call against an agent's prompt),
  `skill_run` (direct skill invocation), `audio_batch` (walk a folder, run the
  same pipeline as `/playground/audio_analyze` on each new file, persist a
  `.openvox_processed` state file so subsequent runs only see new files).
- API: `/api/v1/jobs/{,id,id/trigger,id/runs}`. Dashboard: `/dashboard/schedules`.

### MCP (Model Context Protocol) — `openvox/mcp/`
- Per-agent `mcp_servers` JSON column on `Agent`. Each entry:
  `{name, transport: "stdio"|"sse", command, args, env, url}`.
- `MCPSessionManager` spawns one `mcp.ClientSession` per server at session
  start. Calls `session.list_tools()` and wraps each as a `BaseSkill` with id
  `mcp__<server>__<tool>` — they appear in the LLM's tool-spec alongside
  built-ins.
- Sessions are torn down (subprocess closed) when the WS disconnects. **Always
  call `mcp_mgr.__aexit__()` in the `finally` block.**
- Dashboard validates a config with `POST /api/v1/mcp/probe` before saving.

### Twilio outbound — `openvox/telephony/twilio.py`
- `place_call(to, agent_id, callback_url, lead_id=None, from_number=None)`
  POSTs to `https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json`
  with HTTP Basic auth (`sid:auth_token`). No `twilio` SDK needed.
- `callback_url` must be **publicly reachable** — use ngrok for local dev.
  We append `?agent_id=…&lead_id=…` so the inbound TwiML route can identify
  the session.
- REST endpoint: `POST /api/v1/telephony/twilio/place_call`.
- Scheduler kind `outbound_call_batch` calls top-N leads from a skill
  (default `fetch_next_lead`). **Defaults to preview=true** — set
  `payload.preview=false` to actually dial.

### Multilingual IVR — `openvox/skills/builtin/language.py`
- `detect_language` is **dual-path**: returns the ASR-detected language
  (cheaper) if available on `ctx.metadata.last_language`; otherwise calls
  the LLM to classify the supplied text. Works in both streaming
  (`bigmodel_async`, no auto-detect) and batch (`bigmodel_nostream`) modes.
- `Agent.voice_map: dict[str, str]` maps BCP-47 short codes (`en`, `zh`,
  `es`, …) to BytePlus voice ids. Orchestrator's `_speak()` consults it
  every utterance — uses `voice_id` when no match.
- The orchestrator tracks `_last_language` from each STT result and feeds
  it into the SkillRunner's `ctx.metadata["last_language"]`.

---

## 7. Feature status

### ✅ Shipped & working
- Core async voice pipeline (sentence-flush TTS, interruption, soft TTS-error).
- BytePlus STT (streaming + batch), TTS (HTTP unidirectional), LLM (Ark Seed-2.0).
- BytePlus RAG Cloud client with proper AK/SK signing.
- BytePlus TOS storage backend.
- 14 providers registered (5 LLM + 4 STT + 4 TTS + 1 RTC).
- 25+ skills (general, e-commerce, education, stock, voice analysis, documents,
  reception, sales, language).
- 8 templates: e-commerce-support, education-tutor, stock-analyst, document-qa,
  voice-analyzer, receptionist, sales-sdr, multilingual-support.
- Dashboard pages: Landing, Overview, Playground (Voice/Text/Audio file/Documents tabs),
  Agents (list/new/detail with Behaviour/Voice/Skills/**Documents**/Channels tabs),
  Templates, Providers, Skills, Observability, Settings.
- Multipart upload (audio + documents) via Node gateway → core.
- Document Q&A with embedding-or-BM25 retrieval, vision via Seed-2.0 multimodal.
- Document Q&A voice-in/voice-out (mic record → transcribe → query → synthesize → play).
- Tool-calling end-to-end with proper streaming-fragment accumulation.
- TLS escape hatch for corporate proxies (`OPENVOX_INSECURE_TLS`, `extra-ca.pem`).
- TS + Python SDKs (basic), CLI (`openvox status / agents / templates / skills`).
- **Task scheduler** (APScheduler in-process) — cron/interval/once triggers, three
  job kinds (`agent_query`, `skill_run`, `audio_batch`), DB-backed source of truth,
  dashboard Schedules page with run history. The "every night 8 PM" use case
  works end-to-end.
- **MCP (Model Context Protocol) client** — per-agent server configs (stdio or
  SSE), `MCPSessionManager` connects on session start, tool→skill bridge
  auto-namespaces remote tools (`mcp__<server>__<tool>`). Probe endpoint for
  dashboard validation. New "MCP" tab on the agent edit page.
- **Receptionist / appointment-scheduler template** with 5 calendar skills
  (`business_info`, `check_availability`, `book_appointment`,
  `cancel_appointment`, `list_appointments`) backed by an in-memory demo
  calendar (Acme Salon & Spa, 9-6 Mon-Fri, pre-seeded with a few clashes so
  the agent can demo conflict resolution).
- **Outbound lead qualifier (SDR) template** + Twilio outbound dial-out path.
  Skills: `fetch_next_lead`, `record_disposition` (BANT score), `qualified_leads`,
  `book_demo` (reuses receptionist calendar). Endpoint
  `POST /api/v1/telephony/twilio/place_call`. New scheduler kind
  `outbound_call_batch` for "call top N leads every Monday 9 AM".
- **Multilingual customer-support IVR template** + `detect_language` skill +
  `Agent.voice_map: dict[str, str]` for per-language TTS voice selection.
  Orchestrator's `_speak()` swaps voice based on the last STT result's
  `language` field. Showcases BytePlus's 51-language ASR via
  `enable_auto_lang=true`.

### 🚧 In progress
- (none — Session 6 wrapped: SDR + Multilingual IVR templates shipped.)

### 📋 Designed, queued for next session
See [`docs/PLANNING_NEXT.md`](docs/PLANNING_NEXT.md) for full design:
1. **Scheduler webhook trigger** — fire jobs via
   `POST /api/v1/jobs/webhook/{token}` for event-driven (vs cron) workflows. ~2 hrs.
2. **Skill hot-reload** — file watcher on `~/.openvox/skills/`. ~2 hrs.
3. **Curated MCP server catalogue** — `GET /api/v1/mcp/catalogue` + "Browse"
   view on the MCP tab with one-click "Use this server" pre-fill. ~3 hrs.
4. **CRM-via-MCP for the SDR template** — ship a curated `mcp_servers` snippet
   for HubSpot/Salesforce so users can wire real CRMs in minutes. ~2 hrs.
5. **Backlog**: VAD, S2S, live interpretation, voice podcast, BytePlus RTC
   client wiring, WhatsApp/Telegram inbound message routing, Alembic
   migrations, test suite, GCS/OSS storage, CLI deploy/logs, OAuth.

### ⏳ Pending / roadmap
- **VAD provider** — placeholder. Wire Silero VAD locally + BytePlus VAD when launched.
- **Speech-to-Speech (S2S)** — placeholder. OpenAI Realtime works today as alternative.
- **Live interpretation / translation** — placeholder.
- **Voice podcast generation** (two-speaker) — placeholder.
- **BytePlus RTC client SDK** wiring on the dashboard (token issuance done).
- **Twilio phone calls** — webhook scaffolded; full Media Streams ↔ pipeline bridge pending.
- **WhatsApp Business inbound** — verify + webhook scaffolded; message → agent routing pending.
- **Telegram bot** — webhook scaffolded.
- **Alembic migrations** — currently using `Base.metadata.create_all()` at startup.
- **Comprehensive tests** — none yet (empty `packages/core/tests/`).
- **Storage**: GCS, Alibaba OSS — interface defined, impl pending.
- **CLI**: deploy, logs, dev commands.
- **Auth**: OAuth scaffold in place but disabled (`OPENVOX_AUTH=disabled`).

---

## 8. Bugs already fixed — DO NOT REPEAT

Each entry is a real production bug we tracked down. Future-you, take note.

### Build / Docker / infrastructure
1. **`pnpm` virtual store breaks in Alpine** — symlinks resolve oddly; `tsc` binary
   was "installed" but unfindable. *Fix*: use **npm** for both `packages/server` and
   `apps/dashboard`. Pin `pnpm@9` only when actually needed (workspace-level lock).
2. **`node:20-alpine` ships with `NODE_ENV=production`** — `npm install` skipped
   devDeps. *Fix*: `ENV NODE_ENV=development` in builder stage; runner stage explicitly
   `ENV NODE_ENV=production`.
3. **Docker Compose `${VAR:-default}` lets `.env` override service hostnames** —
   user's local `DATABASE_URL` had `localhost:5432` and broke in-container resolution.
   *Fix*: hard-code `DATABASE_URL` and `REDIS_URL` in `docker-compose.yml` (no `:-`).
   Same for hard-coded `NODE_ENV=production` on server/core (server runner stage
   doesn't have `pino-pretty` installed).
4. **`$` in `.env` JWT_SECRET** is interpreted as variable substitution by
   docker-compose. *Fix*: escape as `$$`.
5. **`pnpm-lock.yaml` missing on first build**. *Fix*: generate once with `npx pnpm@9
   install`, commit. Or use npm where possible (preferred).
6. **`packages/core/README.md` required by `pyproject.toml`** but `.dockerignore`
   excluded `*.md`. *Fix*: copy it explicitly in the core Dockerfile.
7. **Apps/dashboard `public/` directory required** by Next.js but didn't exist. *Fix*:
   create empty dir with `.gitkeep`.

### Fastify v5 / `@fastify/websocket` v11
8. **Handler signature changed**: old API was `(conn, req)` with `conn.socket`;
   v11 hands the WebSocket directly as `(socket, request)`. *Fix*: use `socket.send`
   directly.
9. **`ws` library always delivers `Buffer`** on the server; the `data instanceof Buffer`
   check is always true. *Fix*: discriminate **only** on `isBinary` parameter; text
   frames must be sent as `socket.send(data.toString("utf-8"))`.
10. **Fastify only parses JSON by default** — multipart, octet-stream, text/* all 415.
    *Fix*: register `addContentTypeParser` passthroughs in `server/src/index.ts`.
    Also bump `bodyLimit` to 64 MiB for audio + PDF uploads.

### TLS / network
11. **Corporate TLS inspection (Zscaler) breaks all outbound HTTPS** in containers —
    `unable to get local issuer certificate`. *Fix*: `openvox/utils/http.py` provides
    `make_async_client()` and `certifi_ssl_context()`. Two escape hatches:
    `OPENVOX_INSECURE_TLS=true` (skip verify, dev-only) or drop a corp root PEM at
    `./docker/extra-ca.pem` (auto-mounted, auto-trusted). Used by `httpx` *and*
    `websockets.connect(..., ssl=ctx)`.
12. **`certifi` alone wasn't enough** — even fresh certifi fails when the chain is
    re-signed by Zscaler intermediates. The escape hatches above are the real fix.

### LLM / TTS / STT specifics
13. **BytePlus STT response framing**: 4-byte sequence comes between header and
    payload-size when `flags & 0x01` (default). Old parser misread sequence as size →
    silent garbage. *Fix*: parse `flags`, conditionally skip 4 bytes; also handle
    gzip compression + error frames.
14. **STT result extraction**: text lives at `payload_msg.result.text`, not top-level.
    `definite: true` flags finalised utterances in dual-pass mode.
15. **BytePlus STT clean close** raises `ConnectionClosedOK` from `ws.recv()` after
    server processes last frame. *Fix*: catch and `return`.
16. **TTS resource-ID auto-detection bug**: I tried supporting TTS 1.0 / megatts. User
    directive: **only TTS 2.0**, hardcode `seed-tts-2.0`. Voice catalog is the user's
    responsibility — error 55000000 means "voice not activated on this key".
17. **Tool-calling streaming fragments**: each chunk sends partial `arguments` strings.
    Must accumulate by `index` (`_merge_tool_call_deltas()`).
18. **Tool-call message ordering**: must append assistant message with `tool_calls`
    array *before* tool replies; tool replies use `tool_call_id` (not `name`) to bind.
    Skip this and Ark returns 400.
19. **LLMMessage.content** extended to `str | list[dict]` for vision turns. Update
    serializers if you add new providers.

### Browser audio
20. **AudioContext autoplay**: Chrome puts it in `suspended` even after user click.
    *Fix*: call `ctx.resume()` on each `enqueuePcm16`. Also schedule with 60ms
    look-ahead so first chunk doesn't land in the past (audible click).
21. **Odd-byte buffers** crash `Int16Array` constructor. *Fix*: trim to even length.
22. **Embedding endpoint 404** on user's account (Ark international embeddings not
    available). *Fix*: BM25 fallback in `rag/store.py`. Documents stay queryable.

### Dashboard / TS
23. **TS `if (!agent) return` doesn't propagate into closures** of inner functions
    defined later. *Fix*: use `agent?.x ?? form.x ?? "fallback"` or null-check inside
    the closure.
24. **SWR `mutate` from `useSWR` destructure** can race with `refreshInterval` polling
    when manually called after a delete. *Fix*: use the global `mutate(swrKey)`
    helper for action-triggered refreshes; optimistic remove + revalidate.
25. **Default voice IDs were TTS 1.0 names** (`BV001_streaming`) when the system uses
    TTS 2.0 only. Replaced everywhere. **Migration task**: when defaults change, run
    a `PUT` migration on existing in-DB agent records (`PUT /api/v1/agents/{id}`).
26. **Pydantic `Literal[…]` was too strict** for `BYTEPLUS_LLM_REGION` — user's `.env`
    had AWS-style `ap-southeast-1`, my Literal only allowed `ap-southeast`. *Fix*:
    relax to `str`, normalise in the endpoint resolver (`startswith("cn")`).

### Orchestrator structure
27. **Helper functions defined between class methods** — Python silently parsed
    `_speak`/`interrupt` as inner functions of the helper, breaking
    `VoiceSession._speak`. *Fix*: keep all class methods together, helpers strictly
    after the class definition.

### Schema migrations
28. **`Base.metadata.create_all()` only adds NEW tables, never new columns** on
    existing tables. When we added `Agent.mcp_servers`, existing DBs threw on
    SELECTs. *Fix*: `init_db()` now has a small `_ADDITIVE_COLUMNS` list and
    issues `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for each. Append new columns
    here when you ship them — we'll switch to Alembic once schema churn slows.

### Foreign-key delete cascades
29. **`job_runs.job_id` FK blocked `ScheduledJob` deletion** with
    `ForeignKeyViolationError`. *Fix*: route-level cascade — `DELETE FROM
    job_runs WHERE job_id = $1` before deleting the parent. Could also have
    used `ondelete="CASCADE"`, but that needs a schema migration. Same pattern
    applies to any FK we add later — prefer in-route cascade for now.

---

## 9. Known constraints / environment quirks

The user is running on **macOS behind a Zscaler corporate TLS-inspection proxy**.
This means:
- `OPENVOX_INSECURE_TLS=true` must be in `.env` (or `docker/extra-ca.pem` populated)
  for any outbound BytePlus call to succeed.
- The user's BytePlus voice key (`80fc2961-…`) has **`en_male_tim_uranus_bigtts`**
  activated. Other voices in the catalog return `code=55000000` until activated in the
  console.
- The user's BytePlus LLM key (`6b867f27-…`) speaks Ark `seed-2-0-pro-260328` model
  successfully but **embeddings are 404** — BM25 fallback always engages for documents.
- Their `.env` already has BytePlus TOS credentials (bucket `ankurdemo`) so file
  uploads can use TOS if we ever need a public URL.
- Their workstation has `ffmpeg` available in the core image (we install via apt).

When migrating defaults, in-DB records won't auto-update. There's a script pattern
captured in §2 (`curl … | python3 …`) — keep using it after default changes.

---

## 10. Useful one-liners

```bash
# List existing agents (truncated to first ID)
curl -s http://localhost:3001/api/v1/agents | python3 -c "import json,sys; \
  print('\n'.join(f\"{a['id'][:8]}  {a['name']:30s}  voice={a['voice_id']}\" \
                  for a in json.loads(sys.stdin.read())))"

# Bulk-update voice_id on all existing agents
curl -s http://localhost:3001/api/v1/agents | python3 -c "
import json, sys, urllib.request
for a in json.loads(sys.stdin.read()):
    a['voice_id'] = 'en_male_tim_uranus_bigtts'  # change to taste
    urllib.request.urlopen(urllib.request.Request(
        f\"http://localhost:3001/api/v1/agents/{a['id']}\",
        data=json.dumps(a).encode(),
        headers={'Content-Type':'application/json'}, method='PUT')).read()
    print('updated', a['id'][:8])
"

# Test a tool-call round-trip in the core container
docker compose exec -T core python3 -c "
import asyncio, json
from openvox.providers.bootstrap import register_builtins
from openvox.skills.registry import get_skill_registry
register_builtins(); get_skill_registry()
from openvox.providers import ProviderType, get_registry
from openvox.providers.base import LLMConfig, LLMMessage
from openvox.skills.runner import SkillRunner
from openvox.pipeline.orchestrator import _merge_tool_call_deltas, _finalise_tool_calls

async def go():
    llm = get_registry().get(ProviderType.LLM, 'byteplus')
    runner = SkillRunner(skill_ids=['lookup_order'])
    history = [LLMMessage(role='system', content='Use tools.'),
               LLMMessage(role='user', content='Check order ORD-1001')]
    cfg = LLMConfig(model='', stream=True, tools=runner.tool_specs(), max_tokens=400)
    tc, txt = {}, ''
    async for c in llm.chat_stream(history, cfg):
        if c.tool_calls: _merge_tool_call_deltas(tc, c.tool_calls)
        txt += c.delta
        if c.finish_reason: break
    print('tool_calls:', _finalise_tool_calls(tc))
asyncio.run(go())
"

# Demo orders pre-loaded for testing the e-commerce template:
#   ORD-1001 → shipped, DHL, JD0011223344, ETA in 2 days, Wireless headphones
#   ORD-1002 → processing, Laptop stand
```

---

## 11. Handoff to a future session — checklist

When you (Claude in a fresh session) pick this up:

1. **Read this file first.** Skim sections 6–8 for active context.
2. Check the running services: `docker compose ps`. If something's restarting, check
   logs immediately (§2).
3. If the user reports a bug, **search §8 first** — they often resurface as the user
   experiments with new agents/voices/files.
4. Verify TLS is happy: `OPENVOX_INSECURE_TLS=true` should be in their `.env`. If a
   call fails with cert errors, suggest setting it.
5. The dashboard caches aggressively. After server changes, instruct the user to
   **hard-refresh** (⌘⇧R). Mention this in your reply.
6. After changing default values (voice ID, model name, etc.), **migrate existing
   in-DB agents** with the bulk-update pattern in §10. Don't leave stale records.
7. When adding files, place them according to the layout in §4. Update
   `__init__.py` modules and `__all__` if you add new skill modules.
8. **Compile-check Python after every edit** (§2). Saves a Docker rebuild round-trip.

## 12. Dependencies on the surrounding world

- Internet access from the core container is required for BytePlus calls (LLM/STT/TTS).
- TOS, Postgres, Redis, MinIO are bundled in `docker-compose.yml`.
- Ports 3000/3001/8000/5432/6379/9000/9001 must be free on the host.
- Docker Desktop (or equivalent) on macOS/Linux.
- Optional: ffmpeg already in the core image (for `pydub` audio decoding).

## 13. License & contribution model

Apache-2.0. Single repo, monorepo. Open to extension via:
- Skills: drop a `.py` in `~/.openvox/skills/` or pip-install with the
  `openvox.skills` entry-point.
- Providers: subclass `STTProvider` etc. and register via the `openvox.providers`
  entry-point.
- Templates: dict in `routes/templates.py:TEMPLATES`.

All three plugin points are covered in `docs/extending.md`.
