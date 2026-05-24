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
- **Session 7 polish pass** (2026-05-12/13): every page on the dashboard
  was sanity-checked by an end user; the bug list it surfaced shipped
  in commits 10df997→1cf12a5. Highlights:
  - **Observability now actually has data.** Voice WS + text
    playground both write/update `Session` rows + `Transcript` rows
    so the page is populated after the first turn. `turn_count`,
    `first_token_ms`, `duration_ms`, `status` all tracked.
  - **Top-bar search works.** Fuzzy match across agents / templates /
    skills with keyboard nav and a popover dropdown.
  - **Publish button looks alive.** Busy state + green/red toast +
    optimistic SWR seed so the badge flips draft→published instantly.
  - **Template duplicate guard.** "N created" badge on each card +
    confirm dialog before instantiating another copy.
  - **Agent delete handles attached documents** (in-route cascade).
  - **Skills sanity-check**: 26/26 validated via a one-shot script;
    `get_quote` + `web_search` + `analyze_image` all hit live
    BytePlus / Yahoo / Ark vision endpoints and pass.
  - **All TLS bugs in skills swept** — every outbound HTTP call from
    a built-in skill now routes through `make_async_client`.
- **Session 8 differentiation push** (2026-05-14): nine major items
  shipped end-to-end after competitive research surfaced Dograh as a
  direct OSS rival. Commits: `8d02382` (plan) → `a8c5d79` (checkpoint)
  → `1d4e770` (final).
  - **D.1 Silero VAD + sub-100 ms interrupt**: new `VADProvider`
    interface; orchestrator tees audio to a parallel VAD task; on
    `speech_start` while `_speaking=True` it sets `_cancel_tts`.
    `scripts/measure_interrupt.py` measured **P50=58.5 ms, P95=121.7 ms**.
    Per-agent `vad_provider` column (`silero` | `none`).
  - **D.2 Twilio Media Streams inbound bridge**: full `/ws/twilio` WS
    speaking the connected/start/media/mark/stop/clear protocol with
    μ-law⇄PCM round-tripping via `audioop`. On interrupt we send
    Twilio a `clear` event so already-transmitted audio is dropped.
    Phone-number→agent lookup from `Agent.channels.twilio.phone_numbers`.
  - **D.3 Browser SDK `@openvox/web`**: TS package shipping a React
    `<VoiceAgent />` component + `useVoiceSession` hook. ScriptProcessor
    mic capture (universal, no worklet bundle), PcmPlayer with 60ms
    lookahead. 3 lines to embed in any React app.
  - **A.1 Cross-provider pricing calculator**: `openvox/pricing/` rate
    card for 10 providers + `/api/v1/pricing/{rates,estimate,sessions/{id}}`.
    `sessions/{id}` computes a what-if matrix across STT×LLM×TTS combos.
    Session row gains `llm_tokens_in/out` + `tts_chars` columns
    (additive migration), instrumented in the voice WS forwarder.
  - **A.2 WeChat Work + Lark inbound channels**: `telephony/wechat_work.py`
    with full SHA-1 signature verification + URL-verify handler;
    `telephony/lark.py` with event_v2 challenge + envelope parser.
    Voice-message bridge marked TODO until verified test credentials.
  - **A.3 21 multi-language templates**: 3 use-cases (hotline /
    reactivation / telesales) × 7 languages (EN/ZH/YUE/ES/ID/FR/HI),
    each with **in-language `system_prompt`** (not translated).
    Total catalogue now 29 templates. Language filter chips on the
    Templates page.
  - **C.1 MCP catalogue** (scoped): `openvox/mcp/catalogue.json` with
    6 curated entries (Slack/GitHub/Notion/HubSpot/Salesforce/Stripe).
    `GET /api/v1/mcp/catalogue` + dashboard "Browse catalogue" modal
    that one-click pre-fills the per-agent MCP config form.
  - **B.1/B.2/B.3/B.4 Voice-agent eval framework** *(the wedge)*:
    new `Recording`, `Persona`, `EvalRun` tables. 5 built-in personas
    seeded on startup (angry/confused/ESL/hurry/paranoid). Replay
    runner (recording → LLM-only re-execution) + persona runner
    (two-LLM dialogue, turn cap, natural-end keywords). LLM-as-judge
    with per-criterion strict-JSON output and deterministic Python
    score aggregation. CRUD routes under `/api/v1/evals/*`. Example
    GitHub Action in `.github/workflows/evals.example.yml`; full
    framework guide in `docs/EVALS.md`.
- **Session 9 closeout** (2026-05-18): five of seven priority items
  shipped end-to-end. Commits: `a3b9a63` (#6 + #7) → `384e462`
  (#4 + #2 + #1).
  - **#6 Scheduler webhook trigger**: `trigger_type="webhook"` joins
    cron/interval/once. Token minted on create (idempotent),
    `POST /api/v1/jobs/webhook/{token}` fires the job, optional JSON
    body merges into payload for that single run, disabled/wrong-
    token cases return 200 with `received:false` to prevent
    enumeration. Dashboard renders a `WebhookUrlCallout` with full
    URL + copy-to-clipboard on each webhook job card.
  - **#7 Skill hot-reload**: `watchfiles>=0.24.0`; the new
    `skills/watcher.py` runs `awatch()` filtered to `*.py`, drops
    cached instances, and re-runs `_load_local_folder()` on every
    change. `OPENVOX_SKILLS_DIR` env override for users who want a
    shared volume. Wired into FastAPI lifespan as
    `start_watcher` / `stop_watcher`.
  - **#4 Real provider-reported LLM token usage**:
    `LLMResponseChunk` gains a `usage` field; BytePlus + every
    OpenAI-compat client set `stream_options.include_usage=true`
    so the terminal stream chunk carries `{prompt_tokens,
    completion_tokens, total_tokens}`. Orchestrator emits a new
    `llm_usage` TurnEvent kind. WS forwarder + text playground
    track both word-count `_approx` (always populated) and
    provider `_real` (when emitted); final write prefers
    `_real` when > 0. Pricing calculator now bills against
    actual token counts on the BytePlus / OpenAI / Anthropic /
    DeepSeek / Gemini paths.
  - **#2 Pricing-breakdown card**: Observability rows are now
    clickable → slide-in `SessionDetailDrawer` with a
    `PricingBreakdown` component (stacked STT/LLM-in/LLM-out/TTS
    bar, what-if matrix sorted cheapest-first, "Switch to X to
    save $Y" recommendation). Also surfaces whether the row's
    token counts were provider-reported or duration-estimated.
  - **#1 Evals dashboard page** (`/dashboard/evals`): full UI over
    the eval framework backend. Stats row, recent-runs table with
    verdict + score badge, click-through drawer with per-criterion
    judge breakdown + transcript. `RunEvalModal` lets users spin
    up new runs (agent + persona-OR-recording + criteria + max
    turns). Observability drawer gains a **Save as recording**
    button feeding `/api/v1/evals/recordings/from-session`.
    Sidebar gains the Evals link.
  - **Deferred** (3 of 7 items):
    - **#3 Image-size diet**: PyTorch CPU-only mirror is blocked
      by Zscaler on this machine; retry from unrestricted egress.
    - **#5 WeChat/Lark audio bridges**: blocked on test
      credentials.
    - **Telegram end-to-end test**: blocked on Docker daemon
      being down at the time the rest shipped.

- **Session 10 voice-driven Setup Assistant** (2026-05-18): commit
  `71f47d2`. Six new skills (list_templates, recommend_template,
  instantiate_template, update_agent_field, publish_agent,
  describe_remaining_setup) + a built-in `setup-assistant` template
  + a `/dashboard/agents/new` chooser page (Form / Voice) + a new
  `SetupAssistant.tsx` split-pane component + landing-page CTA +
  `POST /api/v1/agents/{id}/turn` route for text-mode turns. Both
  user decisions landed verbatim: voice + text hybrid input,
  first-class CTA on public landing + topbar.
  **Key design choice that makes voice+text hybrid work**: the
  draft_agent_id stash moved off ephemeral `ctx.metadata` onto the
  Setup Assistant agent's own `channels.setup_state` JSON column.
  Both transports converge on the same persistent state — user can
  speak one turn and type the next without losing context.
  **Verified end-to-end** with four real LLM turns:
  *"I run a salon..." → recommended Receptionist → instantiated
  "Acme Salon" → set greeting → described remaining setup → published.*
- **Session 11 polish pass** (2026-05-18, evening): five commits
  shaking out real-user feedback against the Telegram bot, voice
  agents, and Setup Assistant flow.
  - `bc2d53c` Gateway telephony stubs were swallowing Telegram
    webhooks. Node Fastify had stub handlers for
    `/api/v1/telephony/{telegram,whatsapp,twilio}/*` that returned
    200 OK without forwarding to core. **Removed** the stub
    registration entirely; the catch-all `proxyRoutes` now forwards
    everything to Python. Affected WhatsApp + Twilio paths too —
    they'll work once credentials/numbers land.
  - `46da6a1` Telegram polish: Telegram voice notes arrive as `.oga`
    (OGG/Opus) which `_decode_to_pcm16k` didn't recognise →
    silently failed pydub decode. Now normalised `oga`→`ogg` at
    both the per-call site and the recogniser's ext list.
    **Separately**, `_handle_telegram_update` was calling plain
    `llm.chat()` without `tools=` — agents would hallucinate
    function calls as plain text ("Function call begins, query_documents
    parameters..."). Replaced with the full skill loop, matching
    `/api/v1/agents/{id}/turn`.
  - `d63e429` + `7bdee64` + `8b83dab` TTS quality sweep:
    `openvox/utils/text.py:clean_for_tts` (originally
    `strip_markdown_for_tts`) sanitises **everything** that TTS
    engines mis-pronounce: markdown emphasis, hyphens in compound
    words, URLs, emoji, HTML entities, repeated punctuation. Wired
    into both the orchestrator's `_speak()` (voice WS, playground)
    and `_telegram_synthesize_ogg()`. Companion ASR helper
    `looks_like_real_speech()` rejects background-noise transcripts
    before they hit the LLM. Conservative scope by design: emails,
    file extensions, ampersands, slashes deliberately untouched
    because TTS reads them sensibly in context.
  - `bc31bf1` Three UX bugs from a live playground test:
    (a) stale `"doubao-seed-1.6-250615"` default hardcoded in
    `playground/page.tsx` — fixed by defaulting to `""` and adding
    a placeholder explaining the field. CLAUDE.md §8 #45 lesson
    re-learned (we'd swept the Python sites but missed the TS side).
    (b) Setup Assistant skill created agents with empty `llm_model`
    column. Pre-fill from settings now to match the regular
    /instantiate route.
    (c) Random "voice gets activated every few seconds when idle" —
    open mic + WS left from previous voice session kept transcribing
    background noise → LLM responded → TTS spoke. Fixed via
    aggressive teardown on visibilitychange + pagehide + unmount in
    both playground and SetupAssistant. The playground page had **no
    cleanup useEffect at all** previously.
  - `af6dd8b` Agent delete silently failed for agents with attached
    Session rows. Original cascade only handled Document /
    DocumentChunk — Session 8/9 added five more tables that reference
    agent_id (EvalRun, Recording, ScheduledJob, JobRun via
    ScheduledJob, plus the hard FK on Sessions itself with cascading
    Transcripts). Route now cascades through every reference in
    strict dependency order. Dashboard `destroy()` also gained
    try/catch with alert + explicit `mutate("agents")` invalidation.

### 🚧 In progress
- (none — Session 11 wrapped. Three Session 9 items still gated on
  external dependencies; see PLANNING_NEXT.md.)

### ⏳ Pending / roadmap
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
30. **`documents.agent_id` FK blocked `Agent` deletion** the same way —
    HTTP 500 the moment a user tried to delete an agent that had ever
    ingested a PDF. *Fix*: `routes/agents.py:delete_agent` now does an
    explicit `DELETE FROM document_chunks WHERE agent_id = $1; DELETE
    FROM documents WHERE agent_id = $1;` before deleting the parent.
    (DocumentChunk uses a plain string column, no FK, but we drop them
    too so the RAG store doesn't accumulate orphans.) **Pattern**: any
    new table you add with `ForeignKey("agents.id")` needs a sibling
    cleanup line in this route, or finally bite the schema-migration
    bullet for `ondelete="CASCADE"`.

### Skill / provider gotchas
31. **Skills bypassing the TLS-aware HTTP client** —
    `skills/builtin/stock.py` (`get_quote`) and
    `skills/builtin/general.py` (`web_search`) used a bare
    `httpx.AsyncClient(...)` and silently crashed with
    `CERTIFICATE_VERIFY_FAILED` on the user's Zscaler-intercepted
    network. *Fix*: route through `openvox.utils.http.make_async_client`
    so the `OPENVOX_INSECURE_TLS` + `extra-ca.pem` escape hatches
    apply. **Future-proofing**: `grep -rn 'httpx.AsyncClient'
    packages/core/openvox/skills/` should return zero hits. If you
    add a new skill that makes an HTTP call, use `make_async_client`
    from day one.
32. **Yahoo Finance v7/quote needs a crumb cookie** (started 2024). The
    `get_quote` skill now uses `/v8/finance/chart/{sym}` which works
    unauthenticated and gives us price / previous_close / change /
    exchange / market_state. Don't go back to v7 without implementing
    the crumb dance.
33. **DuckDuckGo `202 No-Instant-Answer`** isn't an error — it's DDG
    saying "this query has no instant-answer panel". `web_search`
    treats 200/202 as success (with potentially-empty fields).
34. **Ark vision endpoint downloads images server-side** — so any
    image URL passed to `analyze_image` must be reachable from Ark's
    IPs and the host must not bot-block. Wikipedia, some CDNs, and a
    handful of S3 buckets with restrictive bucket policies return 403
    to Ark and you get a confusing
    `{"code":"InvalidParameter","message":"Error while downloading: …
    status code: 403"}`. Use BytePlus TOS, picsum.photos, your own
    S3 with public read, or a base64 `data:` URI returned by
    `query_documents`. Skill docstring + `description` both call this
    out so the LLM knows to prefer reachable hosts.

### Dashboard UX
35. **Async action handlers with no busy state look broken** — the
    "Publish" button on the agent detail page made the API call
    correctly but had no spinner, no toast, swallowed errors, and
    only triggered SWR revalidation. Users assumed it had failed.
    *Pattern*: every action button should have `disabled` while in
    flight, a spinner, and a transient banner showing success/error.
    See `agents/[id]/page.tsx` for the canonical pattern.
36. **TurnEvent.data shape mismatch in skill_call** — the
    orchestrator emitted `data=parsed_args` and `_event_to_json`
    spread it into top-level keys, but the dashboard read
    `(ev as any).args`. Result: `→ get_quote({})` even when the
    LLM passed real args. *Fix*: orchestrator now emits
    `data={"args": parsed_args}` so the lookup path matches.
    **Lesson**: when an event type carries structured payload,
    namespace it under a single key — don't lean on spread.
37. **Static `<input placeholder="search">` with no handler** — the
    top-bar search bar had nothing behind it. Now wired to a real
    fuzzy-search popover (agents / templates / skills) with keyboard
    nav and click-outside-to-close. Same pattern applies to any
    "looks like an interactive UI element but isn't" — pick one:
    wire it up or remove it.

### Observability / persistence
38. **No code path was writing to the `sessions` table.** The voice
    WS handler (`api/ws/voice.py`) and text playground endpoint
    (`routes/playground.py`) never instantiated a `Session` row, so
    the Observability page rendered "0 sessions" forever no matter
    how many turns ran through the playground. *Fix*: both paths now
    write a Session row up-front + finalise duration / turn_count /
    first_token_ms on completion (best-effort, try/except so DB
    hiccups never kill the chat itself). Text path also accepts
    `agent_id` so users can target a specific agent. **Lesson**: if
    a dashboard page reads from a table, *grep for writers to that
    table* — empty UI is usually a missing producer, not a missing
    consumer.

### Template instantiation
39. **No idempotency on "Use template" clicks** — repeatedly clicking
    the same template card created duplicates (we ended up with 3
    "Acme Support Voice" and 2 "Audio Analyzer"). *Fix in dashboard*:
    each template card shows a green "N created" badge if matching
    agents exist; clicking "Use template" when N > 0 pops a confirm
    dialog with the existing agent names — OK opens the first one,
    Cancel falls through to create a fresh copy. The flow is
    *informed* but still allows intentional duplicates. We didn't
    add a DB unique constraint because users legitimately want
    multiple agents from the same template with different prompts
    /voices.

### Build / deploy discipline (Session 8 lessons)
40. **Dashboard rebuild is its own step.** When you edit
    `apps/dashboard/src/**`, the running dashboard container keeps
    serving the previous bundle. Today's "MCP catalogue button is
    missing" was exactly this — code shipped, source on disk, but
    the Next.js production build in the container was stale.
    Pattern: **any TSX/TS change requires
    `docker compose build dashboard && docker compose up -d --no-deps dashboard`**,
    same as core needs a rebuild for Python changes. Hot-reload
    isn't wired in production builds.
41. **`docker cp <local-dir> <container>:<existing-dir>` nests.**
    If the destination already exists, `docker cp packages/core/openvox
    openvox-core:/app/openvox` creates `/app/openvox/openvox/` rather
    than overwriting `/app/openvox/`. Use `<local-dir>/.` trailing-dot
    syntax instead: `docker cp packages/core/openvox/. openvox-core:/app/openvox`.
    Bit us on the Silero registration probe before we noticed —
    container kept loading the *old* bootstrap.py while the new one
    sat at `/app/openvox/openvox/providers/bootstrap.py` unused.
42. **`silero-vad` hardcoded `onnx=True` requires onnxruntime.**
    Our first cut of `providers/vad/silero.py` passed `onnx=True`
    unconditionally; on an image with only torch (silero-vad's
    transitive dep) this fails with `No module named 'onnxruntime'`.
    *Fix*: default to torch backend, flip to ONNX only when
    `OPENVOX_VAD_BACKEND=onnx` is set AND onnxruntime is installed
    separately. The 5 ms speed-up isn't worth the dep CVE surface.
43. **Docker Hub registry mirror flakes break builds.** Mid-session
    Docker Desktop's BuildKit returned
    `Get "registry-1.docker.io/v2/.../manifests/..." ... dial tcp
    127.0.0.1:9000: connect: connection refused`. This is a Docker
    Desktop registry-mirror config issue, not anything in our code.
    Workarounds: retry, switch to `DOCKER_BUILDKIT=0`, or `docker cp`
    the new source into a still-running container (see #41 caveat).
44. **CPU-only torch wheel saves ~3 GB image space but the mirror is
    Zscaler-blocked.** Tried `pip install --index-url
    https://download.pytorch.org/whl/cpu --no-deps torch torchaudio`
    to slim the core image. Works on unrestricted egress; fails here
    with `Failed to fetch: https://download.pytorch.org/whl/cpu/...`.
    Dockerfile leaves the optimization wrapped in `|| true` so the
    main install still pulls the standard CUDA wheel as a fallback.
    Future-you: re-attempt when network policy allows.

### Defaults drift + recursion bombs (Session 9 review catches)
45. **Stale default LLM model in three places.**
    `SessionConfig.llm_model`, `playground.TextRequest.model`, and
    the voice WS ad-hoc fallback all hard-coded
    `"doubao-seed-1.6-250615"` — a model name that doesn't exist on
    our BytePlus key (the real default lives in
    `settings.byteplus_llm_model = "seed-2-0-pro-260328"`). Every
    caller that didn't pass an explicit `model=` was silently hitting
    the wrong endpoint, and BytePlus's behaviour in that case is
    implementation-defined (sometimes 400, sometimes auto-route).
    Could have been costing real money against a model the user
    doesn't intend to use.
    *Fix*: all three defaults are now empty string, matching the
    canonical `Agent.llm_model = ""` pattern. The provider's
    `_model_id(requested) → requested or self._default_model` then
    resolves to the configured model.
    **Lesson**: when you add a *fallback* default, make it
    something that defers to the same source of truth used elsewhere
    (env / settings). Hard-coded literals drift the moment the real
    default changes.
46. **`VoiceSession._llm_turn` recursed on every tool-call round**
    with no depth limit. One buggy or adversarial skill that always
    triggers another `tool_call` → unbounded Python recursion →
    stack overflow → session dies. Particularly risky with
    user-controlled MCP servers, which are external processes that
    could deliberately or accidentally loop.
    *Fix*: replaced the `async for ev in self._llm_turn(): yield ev`
    recursion with a bounded
    `for iteration in range(self._cfg.max_tool_iterations):` loop
    (default 6). On overflow we emit a clear `error` TurnEvent
    rather than crash. Per-agent override possible via
    `SessionConfig.max_tool_iterations`.
    **Lesson**: any LLM ↔ tool round-trip is recursive by nature
    (LLM may call more tools after seeing results). Cap it. Six is
    high enough for legitimate chains (SDR
    `fetch_next_lead → record_disposition → book_demo` is four),
    low enough to fail fast on tight loops.

### Telephony / proxy gateway pitfalls (Session 11)
47. **Node gateway stubs swallowed Telegram/WhatsApp/Twilio webhooks.**
    `packages/server/src/routes/telephony.ts` had `fastify.post(
    "/telegram/webhook", async () => ({forwarded: true}))` and
    similar stubs for the WhatsApp + Twilio webhooks. Mounted under
    the `/api/v1/telephony` prefix AFTER the catch-all `proxyRoutes`,
    Fastify's route-specificity match meant the stubs intercepted
    every webhook delivery. Telegram saw 200 OK + a synthetic
    response → cleared its queue → user wondered why messages
    delivered to `@their_bot` never reached the agent. Four real
    user messages were lost before we noticed.
    *Fix*: remove the `telephonyRoutes` registration entirely. The
    Python core has the real implementations; the catch-all proxy
    now forwards `/api/v1/telephony/*` straight through.
    **Lesson**: the gateway is a transparent proxy. If you ever add
    a route in `packages/server/src/routes/` that overlaps with the
    `/api/v1/*` prefix, you're shadowing core. Either delete it or
    delegate to core via an explicit `fetch()` call — don't return
    a synthetic response from the gateway.

### Audio / TTS / ASR quality (Session 11)
48. **Telegram voice notes arrive as `.oga`, not `.ogg`.**
    Telegram's `voice` Update payload returns files with extension
    `.oga` (OGG container, Opus codec). `_decode_to_pcm16k`'s
    recognised extension list had `ogg` but not `oga`, so format
    detection fell through to `None` and ffmpeg refused to guess:
    `code 183: Invalid data found when processing input`. The
    transcribe helper then returned `""` and the user got the
    fallback "I couldn't make out what you said".
    *Fix*: normalise `oga → ogg` at both the per-call site in
    `_telegram_transcribe` and the recogniser's ext list in
    `_decode_to_pcm16k`. Belt + braces.
    **Lesson**: when accepting audio from any third-party API,
    normalise the filename extension yourself — don't trust the
    source's naming conventions.
49. **`llm.chat()` without `tools=` is a footgun for agents whose
    `system_prompt` mentions skills.** The Telegram text path used
    a plain `llm.chat(messages, cfg)` call without passing
    `tools=runner.tool_specs()`. When the Doc Assistant's prompt
    told the LLM to use `query_documents`, the LLM dutifully
    "described" the call as plain text in the response. That text
    then got TTS-synthesized: "Function call begins, they do not
    name query_documents parameters query list of all APIs."
    *Fix*: replace the bare chat with the full skill loop — same
    shape as `orchestrator._llm_turn` and the
    `/api/v1/agents/{id}/turn` route. Bounded at 6 iterations.
    **Lesson**: every text-mode transport (Telegram, WeChat, Lark,
    any future channel) must run the skill loop, not a one-shot
    `chat()`. Add this to the channel-bring-up checklist.
50. **Raw LLM text to TTS is a quality disaster.** LLMs write for
    readers, not listeners. Markdown emphasis (`**bold**`), URLs,
    emoji, repeated punctuation, hyphens in compound words —
    every one of these reads wrong out loud. We hit each in turn
    over a single test session:
    - `**ListAssets**` → "asterisk asterisk ListAssets" (commit `d63e429`)
    - `real-human guide--ModelArk` → "real dash human guide dash dash
       ModelArk" (`7bdee64`)
    - `https://docs...` → "h-t-t-p-s-colon-slash-slash"
    - 🎉 emoji → "white heavy check mark"
    - `&amp;` → "ampersand a m p semicolon" (`8b83dab`)
    *Fix*: centralised `openvox/utils/text.py:clean_for_tts()`
    sanitises every voice-hostile pattern. Wired into orchestrator
    `_speak()` AND `_telegram_synthesize_ogg()` so every TTS-emitting
    path gets the same treatment.
    **Lesson**: never feed raw LLM text to a TTS engine. Always
    sanitise first. New TTS code paths (future WeChat audio, Lark
    audio, Twilio outbound) must route through `clean_for_tts`.
    Conservative-by-default — we deliberately leave email
    addresses, file extensions, ampersands, slashes alone because
    TTS engines pronounce those acceptably in context.

### Dashboard state hygiene (Session 11)
51. **Stale defaults can persist in TypeScript state even after a
    Python sweep.** Bug #45 fixed three Python sites that hardcoded
    `"doubao-seed-1.6-250615"` as the LLM model default. Missed
    `apps/dashboard/src/app/dashboard/playground/page.tsx:27` which
    had the same string as the initial form state. Result: the
    Playground voice tab silently overrode every ad-hoc session's
    model to a name that doesn't exist on the user's BytePlus key.
    *Fix*: default to empty string + placeholder "(use provider
    default from .env)" in the field.
    **Lesson**: when sweeping a stale literal, search the whole
    monorepo (`grep -rn "doubao-seed-1.6"`), not just one language.
    Add a pre-commit lint or a CI step that catches re-introduction.
52. **Voice sessions leak across page navigation.** The playground
    page had **no cleanup useEffect at all**, so leaving the page
    while the mic was active left the WebSocket open and the mic
    capturing background audio. BytePlus STT transcribed ambient
    noise as garbage utterances → LLM responded → TTS played.
    User-facing symptom: "every few seconds voice gets activated"
    on unrelated dashboard pages.
    *Fix*: aggressive teardown on every navigation path — unmount
    AND `visibilitychange` AND `pagehide` listeners — applied to
    both the Playground page and the Setup Assistant component.
    **Lesson**: every component that opens a WebSocket OR captures
    mic OR holds an AudioContext needs visibility/unload teardown,
    not just unmount. Next.js client navigation doesn't always
    unmount eagerly, and even when it does, browser tab-switching
    doesn't trigger unmount.

### Foreign-key cascades (recurring family)
53. **Agent delete missed five new tables added by Session 8/9.**
    Bug #30 expanded the delete route to handle Documents +
    DocumentChunks. Sessions 8/9 then added EvalRun, Recording,
    ScheduledJob (+ JobRun via ScheduledJob.id), and the Sessions
    hard FK with cascading Transcripts. None were covered. User
    hit "Delete agent" + confirm → silent failure (the route
    threw `ForeignKeyViolationError` on the Sessions FK, the
    dashboard caught it but didn't surface the error, `router.push`
    never ran).
    *Fix*: route now cascades through eight tables in strict
    dependency order: EvalRun → Recording → JobRun → ScheduledJob
    → Transcript → Session → DocumentChunk → Document → Agent.
    Dashboard `destroy()` also got try/catch with `alert()` so
    future failures surface instead of disappearing.
    **Lesson**: this is the THIRD FK-cascade bug in the register
    (#29, #30, #53). Every new table that references `agents.id`
    or `sessions.id` must update the relevant delete route. Worth
    a one-time schema migration to add `ondelete="CASCADE"` on
    every such FK so future tables don't need this dance — listed
    in PLANNING_NEXT.md.

### Fastify v5 strict spec compliance (recurring family)
54. **Dashboard `http<T>` helper always attached
    `Content-Type: application/json`** — even on DELETEs and other
    bodyless requests. Fastify v5 enforces RFC 7231 strictly: if you
    declare `Content-Type: application/json` you must supply a JSON
    body. Empty + declared = `FST_ERR_CTP_EMPTY_JSON_BODY` 400 at
    the gateway, before the request ever reaches core. User clicked
    Delete on an agent → got the Fastify error string verbatim in
    an alert (which we surface since #53's `destroy()` improvements,
    so at least we saw the bug now).
    *Fix*: in `apps/dashboard/src/lib/api.ts:http`, only set
    Content-Type when `init.body` is truthy. Caller-supplied
    headers still honoured for explicit overrides. Side benefit:
    `publishAgent` (a bodyless POST) was probably tripping the
    same error silently — now it doesn't.

    **The Fastify-v5-strict-spec family is now at FOUR bugs**:
55. **Voice WS + Telegram never wrote `Transcript` rows.** Bug #38
    half-fixed this by adding the `Session` row to the voice WS and
    text playground. Text playground also writes per-turn `Transcript`
    rows (lines 89 + 133 of `routes/playground.py`); voice WS and
    Telegram did NOT. Symptom: every voice session in Observability
    rendered with no turn-by-turn detail, and "Save as recording" →
    Replay-eval recordings showed "0 turns" in the dropdown. Starting
    a replay eval fed an empty transcript to the candidate → judge
    correctly failed both criteria with "No agent dialogue appears in
    the provided empty transcript".
    *Fix*: `api/ws/voice.py:_forward_events` now persists a `Transcript`
    row for each `user_final` and `assistant_done` event scoped to
    the session's `db_session_id`. `api/routes/telephony.py:
    _handle_telegram_update` now creates a `Session` row up-front +
    writes user/assistant Transcripts + finalises the row on
    completion (mirrors the voice WS lifecycle).
    **Lesson — extension of #38**: "if a dashboard page reads from a
    table, grep for writers." But also: "if a *feature* reads from a
    table (replay eval reads Transcript), every channel that produces
    that feature's input must write to it." When you add a new channel
    (next up: WeChat/Lark voice bridges), the channel-bring-up
    checklist must include Session + Transcript persistence, not just
    "the LLM responds correctly".
    - #8  handler signature changed (`(socket, request)` not `(conn, req)`)
    - #9  `ws` library Buffer discrimination via `isBinary` flag
    - #10 `addContentTypeParser` passthrough for multipart/octet-stream
    - #54 don't declare Content-Type on bodyless requests
    **Meta-rule**: any time you add a new HTTP client or change
    request shapes on the dashboard / SDK side, run through this
    checklist:
      1. Bodyless requests (GET, DELETE, bodyless POST like
         `/publish`) → no Content-Type header.
      2. Multipart uploads → core needs
         `addContentTypeParser("multipart/*", ...)` passthrough.
      3. Octet-stream / text/plain → same passthrough required.
      4. WS frames → discriminate on `isBinary` parameter, never
         on `data instanceof Buffer` (always true in Node WS).
    Fastify is opinionated about spec compliance. Better to learn
    that with intentional checks than to ship a 400 to a user.

### Pricing model misfits (Session 12)
56. **Hard-coded rate card had no cited sources.** Original
    `pricing/rates.py` was written from training-data recollection;
    a user reading the recommendation tip ("switch to deepseek to
    save 16%") had no way to verify the underlying numbers. DeepSeek
    in particular was suspiciously low ($0.14/$0.28 — that's the
    pre-Azure-markup official rate). Worse, **BytePlus voice was
    being modelled as per-minute STT and per-1k-chars TTS** when the
    actual BytePlus billing is **per-character on both** ($50/1M
    chars ASR, $45/1M chars TTS — verified against
    `docs.byteplus.com/en/docs/byteplusvoice/{asrbilling,TTS_Billing}`).
    Effect: cost shown for any session on BytePlus voice was
    understated by ~3.5×, and the "Switch to X to save $Y" tip was
    pointing at fabricated savings (Deepgram hardcoded at $0.0043/min
    when the real rate is $0.0077/min — Deepgram is actually MORE
    expensive than BytePlus voice on a per-minute view).
    *Fix*: ProviderRates dataclass gains `model_name`, `source_url`,
    `verified_at`, and `stt_usd_per_1m_chars`. Every rate refreshed
    against the live provider page on 2026-05-19 with citations
    embedded. The dashboard PricingBreakdown card surfaces unit
    hints ("$50/1M chars · Seed ASR 2.0") under each component pill
    and a "Rate sources" expander with click-through `source_url`
    links + `verified_at` dates. Sessions older than this fix get a
    `stt_chars_estimated: true` flag in their telemetry so users know
    the ASR cost was proxied from `tts_chars` (symmetric-conversation
    assumption) rather than measured.
    **Lesson**: any number that drives a recommendation MUST have a
    cited source on the read path. The user spotted this exactly
    because they noticed the tip was implausibly cheap. If we'd
    surfaced "source: training-data estimate, unverified" from day
    one, neither of us would have wasted time on the bad
    recommendation.
57. **`stt_chars` wasn't tracked anywhere.** Bug #56's fix to model
    BytePlus ASR as per-character revealed that we had no measured
    character count on the read path — only `tts_chars`. The
    pricing route proxied STT chars from TTS chars on the
    symmetric-conversation assumption, which is wrong for any
    asymmetric dialogue (user grunts "yes" while agent monologues,
    or vice versa).
    *Fix*: `Session.stt_chars` column added (additive migration via
    `_ADDITIVE_COLUMNS`). Voice WS forwarder accumulates user_final
    char counts into `metrics["stt_chars"]`; Telegram handler writes
    `len(user_text)`. Text playground stays at 0 (no STT happened).
    Pricing route uses `sess.stt_chars` when > 0, falls back to
    `tts_chars` proxy with the `stt_chars_estimated: true` flag.
    Matrix builder now treats a provider as a valid STT option if
    EITHER `stt_usd_per_minute` OR `stt_usd_per_1m_chars` is set —
    previously BytePlus dropped silently out of the matrix once we
    modelled it per-char.
    **Lesson**: every billable counter on the read path needs an
    explicit field on the write path, even if it overlaps
    conceptually with another counter. "User and agent talk roughly
    the same amount" is a reasonable heuristic; "exactly the same
    amount" is wrong for billing.

### Template voice drift (Session 12)
58. **Science Tutor template shipped with an unactivated voice.**
    `templates.py` set `voice_id="en_male_adam_mars_bigtts"` for the
    Education Tutor template. The user's BytePlus key only has
    `en_male_tim_uranus_bigtts` licensed (§9). Result: every voice
    turn in the playground returned the documented
    `code=55000000 message='resource ID is mismatched with speaker
    related resource'` error. Bug #25 in spirit (we *knew* this
    voice family was finicky) but a new instance because the
    template was added without checking the activated-voice list.
    *Fix*: template now uses the same `en_male_tim_uranus_bigtts`
    default as every other English template. Migrated in-DB agents
    via the bulk-PUT pattern from §10.
    **Lesson**: when adding a new template that sets `voice_id`, the
    chosen voice MUST be in the activated-on-user-key list. Until
    we ship a "list licensed voices" endpoint + dashboard validator,
    default to `en_male_tim_uranus_bigtts` and let the user override
    later. Multi-language templates (zh, yue, es, id, fr, hi voices)
    are *intentionally* exempt — they need language-appropriate
    voices by design, and the user activates them per-demo.
59. **Multilingual templates referenced fabricated voice IDs.**
    Reading from `templates.py:_LANG_META` the zh/yue voices used
    `zh_female_qiniao_bigtts` and `zh_female_cantonese_bigtts` (both
    TTS 1.0-era names that don't exist in the current TTS 2.0
    catalogue), and the es/id/fr/hi voices used `multilingual_v2_*`
    IDs which are **ElevenLabs** voice IDs accidentally fed to the
    BytePlus TTS provider. Result: every multilingual template
    instantiation gave `code=55000000` at TTS time, identical
    symptom to bug #25/#58 but a completely different root cause
    (invented IDs vs licensed-but-wrong IDs).
    *Fix*: created `providers/byteplus/voices.py` with the full TTS
    2.0 catalogue (41 voices, refreshed against `ModelMD/TTS2_voices.md`
    on 2026-05-19). `_LANG_META` now uses real IDs from that
    catalogue. `GET /api/v1/providers/voices` returns the full
    catalogue so the dashboard agent-edit form can render a
    **dropdown** (`VoiceSelector` component) — typos like `_qiniao_`
    are now structurally impossible. Also added a "Test voice"
    button that POSTs to `/playground/synthesize` and plays the
    returned PCM, so users can confirm activation per-voice without
    starting a full voice session.
    Hindi (`hi`) caveat: TTS 2.0 has no native Hindi voice. The
    Hindi template now uses Vivi (multilingual; covers en/zh/ja/es/id
    but **not hi**) — Hindi text will read as transliterated
    English. To fix properly, route hi-IN agents through the
    ElevenLabs TTS provider with `multilingual_v2_*` voice IDs
    (which is what the original code accidentally tried to do — it
    just stuffed those IDs into BytePlus instead of switching
    provider).
    **Lesson**: any voice/model/provider ID hardcoded into a
    template must be validated against the receiving provider's
    catalogue at code-write time. The catalogue module exists for
    this — `is_known("...")` / `voices_for_language("...")` make
    validation trivial. Run them in a unit test on every template
    file (TODO).

### Session 12 onward — UX polish + voice-quality overhaul

60. **APScheduler `from_crontab()` uses a non-standard day-of-week
    convention.** Session 12 added a "Simple" mode to the Schedules
    UI that translated `Repeat: Weekly` on a Saturday-picked date
    into cron `0 8 * * 6` — JS `getDay()` returns 6 for Saturday,
    matching Unix cron's Sun=0..Sat=6. But `CronTrigger.from_crontab()`
    forwards the 5th field straight to `CronTrigger(day_of_week=…)`
    which uses Mon=0..Sun=6. So our `dow=6` was interpreted as
    Sunday, and the schedule fired on Sundays. *Fix*: remap JS dow
    → APS dow via `(jsDow + 6) % 7` in the client-side translator
    (`apps/dashboard/src/app/dashboard/schedules/page.tsx`).
    **Lesson**: every cross-language day-of-week boundary is a trap.
    Verify by setting a real Saturday date in the UI and confirming
    the resulting `next_run_at` from the server is also a Saturday
    — visual end-to-end check, not just the cron string.

61. **BytePlus Seed-ASR streaming defaults to Chinese on silence
    when the request omits `language`.** Symptom: `嗯。`, `啊。`,
    `哦` appeared as USER turns mid-conversation in English voice
    sessions on the SetupAssistant. Confidence filter never caught
    them — Seed reported either `confidence: 0.0` (no value) OR
    `>= 0.5` for its own filler hallucinations. The streaming
    `start` payload our `byteplus/stt.py` sends had `model_name`,
    `enable_itn`, `enable_punc`, `end_window_size` — but no
    `audio.language` field. Without it, Seed runs in auto-detect
    mode and falls back to Chinese on breath / lip smack / room
    tone. The batch endpoint (`transcribe_file_url`) already set
    `audio.language` correctly; the streaming endpoint just hadn't
    been wired the same way. *Fix*: send `start["audio"]["language"]
    = config.language` whenever non-empty + INFO-log
    `stt language hint: <lang>` per session. Defence-in-depth in
    `pipeline/orchestrator.py` `sanitize_user_final()`: trim leading/
    trailing filler chars + CJK punctuation so `嗯。create` → `create`;
    drop pure-filler or ≤3-char pure-CJK finals on non-zh agents
    (passes through unchanged on zh-* agents).
    **Lesson**: when a provider has a `language` field, send it
    EVERYWHERE you use that provider, on both streaming and batch
    paths. Audit at the provider-module level, not the endpoint
    level. Every multilingual model has a "default if absent"
    fallback and it is rarely the language you actually want.

62. **Reasoning-model close tags carry per-session hash suffixes
    that break naïve regex stripping.** Seed-2-Pro emits chain-of-
    thought inside `<think>…</think_HASH>` blocks where `HASH` is a
    32-char hex random per session (anti-prompt-injection — the
    user can't fake a close because they don't know the hash).
    Result: `</think_never_used_51bce0c785ca2f68081bfa7d91973934>`
    landed in the chat display, TTS spoke it as "less than slash
    think underscore never underscore used…", and the LLM history
    accumulated reasoning across turns (input cost ballooned, model
    started repeating itself). Streaming token boundaries also
    split tags mid-name (`<th` + `ink_neve` + `r_used_…>`), so a
    per-chunk `re.sub(...)` leaks fragments. *Fix*: new streaming
    state machine `ReasoningStripper` (`openvox/utils/text.py`) that
    holds back tag-boundary text until the open or close resolves.
    Wired into `orchestrator._llm_turn()` so display / TTS buffer /
    history all see only clean text. `clean_for_tts` defensively
    re-strips via `strip_reasoning_tags()` for non-streaming
    callers (telephony, /turn endpoint). Open and close regex is
    `<(think|reasoning)([^>]*)>` so future variants (`<thinking>`,
    `<reasoning_v2>`, hash-suffixed open like `<think_HASH>`) are
    covered.
    **Lesson**: any reasoning-capable LLM provider needs token-stream
    filtering at the orchestrator boundary, not at the display
    layer. Three boundaries that must see clean text: chat UI,
    TTS, and LLM history — miss any one and the bug returns.

63. **Singleton endpoints serve point-in-time snapshots; template
    changes don't propagate to existing agent rows.** Session 13
    added a `create_custom_agent` skill + workflow branch to the
    Setup Assistant. Wired and tested via the `singleton` GET, but
    the LLM kept calling the OLD tools because the running Setup
    Assistant agent (created at first GET) had the OLD `system_prompt`
    + `skills` snapshot from when it was first instantiated. The
    template's `default` dict only feeds `instantiate_template`
    once — after that the Agent row is decoupled. *Fix*: the
    `setup_assistant_singleton` endpoint now reconciles `system_prompt`,
    `greeting`, and `skills` against the current template defaults
    on every GET. Voice_id / temperature / max_tokens / llm_model
    stay untouched (those are owner-tunable; the user may have
    customised them deliberately). INFO-log on every resync:
    `Setup Assistant <id> re-synced from template defaults`.
    **Lesson**: built-in/canonical/managed agents need a sync
    policy. Per-field decide: "definition" fields (prompt, skills,
    greeting) → resync from template; "tuning" fields (voice,
    temperature, model) → leave alone. Document the policy in
    template comments so it's not relitigated each session.

64. **Server-side VAD-based barge-in fails silently when browser AEC
    isn't perfect.** Silero VAD is registered, the orchestrator's
    `_vad_loop` is running, `push_audio` tees to `_vad_inbound`,
    `_speaking == True` during TTS — all the plumbing is right.
    But `speech_start` never fires when the user starts talking
    during TTS playback. Hypothesis: browser's getUserMedia echo
    cancellation isn't clean enough to remove the TTS bleed-through,
    so VAD stays in `in_speech` from the moment TTS starts (it
    treats the TTS audio coming back through the mic as
    "continuing speech"), never sees a silence→speech transition
    to fire on. *Workaround (not a real fix)*: add TWO client-side
    interrupt paths that don't depend on VAD: (a) visible Stop
    button during TTS playback that sends `{"type":"interrupt",
    "source":"button"}` over WS; (b) browser-native `webkitSpeech
    Recognition` listener running in parallel with TTS, matching
    stop-words `stop|pause|wait|halt|cancel|quiet|hold on|be quiet`
    (+ 停/停下/暂停) and firing the same interrupt path with
    `source="voice"`. Both feed `session.interrupt()` which sets
    `_cancel_tts` — the existing backend path was always wired
    correctly, the trigger was the missing piece. Backend logs
    `interrupt requested via WS (source=...)` so future "stop
    didn't work" reports are debuggable.
    **Real fix deferred** to a future session: either server-side
    echo subtraction (subtract outgoing TTS PCM from incoming mic
    PCM by sample) or continuous STT during TTS so stop-words
    detect server-side. Track as Session 13 open follow-up.
    **Lesson**: when the VAD probe never fires in production, the
    issue is almost never the VAD model — it's the input audio's
    SNR / echo profile vs what VAD was trained on. Always layer a
    deterministic non-acoustic trigger (button, hotword via
    independent recogniser) before relying on VAD alone for any
    user-visible feature.

65. **Relative `/api/v1/...` URL in a dashboard `fetch()` lands at
    the Next.js dev server's 404 page, NOT the backend.** The
    Agents → Voice & model → "Test voice" button used `fetch
    ("/api/v1/playground/synthesize", ...)`. The dashboard runs on
    :3000 (Next.js); the API gateway is on :3001. A relative URL
    resolves against the page origin → request never leaves
    Next.js → Next.js has no such route → returns its default 404
    HTML body. The dashboard then tried to play the `<!DOCTYPE
    html>...` page as PCM audio. *Fix*: route through the existing
    `api.synthesize()` helper in `apps/dashboard/src/lib/api.ts`
    which builds `${BASE}/api/v1/...` from `NEXT_PUBLIC_API_URL`
    (or the `http://localhost:3001` fallback). The hand-rolled
    `fetch` also passed a `tts_provider` field the backend's
    `SynthesizeRequest` pydantic model didn't have — silently
    ignored, dropped on the floor at refactor time.
    **Lesson**: there should be ONE place in the dashboard that
    knows the backend's base URL — `lib/api.ts`. Anything that
    bypasses it is a relative-URL bomb waiting to surface.
    Grepped the whole dashboard for sibling bugs (`fetch("/api/v1`)
    — zero hits this time, but a CI/lint rule that forbids the
    pattern is worth adding.

### Session 15 — Phase 2 channels + Phase 1 PR-1 stack diet

66. **Puppeteer cache-path failure inside Docker.** The WhatsApp
    Personal bridge (whatsapp-web.js → Puppeteer → Chromium) failed
    on first launch with:
        `Could not find Chrome (ver. 146.0.7680.31). This can occur if
         either 1. you did not perform an installation before running
         the script (e.g. npx puppeteer browsers install chrome)
         or 2. your cache path is incorrectly configured
         (/root/.cache/puppeteer).`
    Root cause: my original Dockerfile installed all of Chromium's
    runtime libraries (libgtk-3-0, libnss3, libxshmfence, etc.) but
    didn't install Chromium itself — relying on Puppeteer's auto-
    downloader. That downloader's cache conventions (binary name +
    version subdirectory under `~/.cache/puppeteer/`) shift between
    Puppeteer versions and don't survive Docker layer-cache cleanly.
    *Fix*: install `chromium` from Debian apt directly + set
    `PUPPETEER_SKIP_DOWNLOAD=true` + `PUPPETEER_EXECUTABLE_PATH=
    /usr/bin/chromium`. Also explicitly pass `executablePath` to
    `puppeteer.launch()` in `index.js` — whatsapp-web.js's bundled
    puppeteer instance ignores the env var. apt's chromium also
    handles its own library transitive deps, so the Dockerfile
    shrinks from 20+ explicit libs to just `chromium +
    fonts-liberation + ca-certificates`.
    **Lesson**: when running Puppeteer inside Docker, NEVER rely on
    Puppeteer's auto-downloader. Always pin to a known-installed
    Chromium (apt for Debian/Ubuntu images, alpine-chromium for
    Alpine). Set both env var AND explicit `executablePath` —
    libraries that embed their own puppeteer instance often ignore
    the env var.

67. **Zscaler TLS interception breaks Chromium too.** After fixing
    bug #66, the next error was
    `ERR_CERT_AUTHORITY_INVALID at https://web.whatsapp.com/` — same
    root cause `OPENVOX_INSECURE_TLS=true` solves on the Python side
    (corporate proxy injects its own CA, Chromium rejects unknown
    CA). *Fix*: bridge's `index.js` reads `OPENVOX_INSECURE_TLS`
    from env; when truthy, appends `--ignore-certificate-errors` to
    Chrome's launch args and logs the trade-off prominently.
    `docker-compose.yml` passes the variable through to the bridge
    so the existing `.env` toggle just works.
    **Lesson**: when adding new components that make outbound HTTPS
    calls (especially headless-browser components), check for the
    EXISTING `OPENVOX_INSECURE_TLS` toggle and respect it. Users
    behind corporate proxies have already opted into the trade-off
    at the OS / network level; making each new component reinvent
    the toggle is annoying.

68. **Stale Chromium profile lock blocks bridge restart.** When the
    WhatsApp bridge subprocess died mid-launch (Chromium crash, the
    container OOM'd, etc.), the next start fails with:
        `The profile appears to be in use by another Chromium process
         (38) on another computer (28fcd1b7c13e). Chromium has locked
         the profile so that it doesn't get corrupted.`
    The `SingletonLock` file inside `/data/sessions/<agent_id>/` is
    leftover from the crashed process. Recovery today is manual:
        `docker volume rm openvox_whatsapp-sessions`
    which wipes the volume + forces a fresh QR scan. Quick fix
    deferred to follow-up: on bridge startup, `rm -f` the
    SingletonLock files before initialising any client.
    **Lesson**: any Chromium-based subprocess in a container needs
    crash-resilient startup. The "is the profile locked" check is
    designed for cross-machine cases where lock truly matters; in a
    single-container deployment it just gets in the way.

69. **FastAPI rejects union return-type annotations.** Porting the
    auth scaffolds from the Node gateway to FastAPI in Phase 1 PR-1,
    I wrote:
        `async def github_start() -> JSONResponse | RedirectResponse:`
    FastAPI errored at import with:
        `Invalid args for response field! Hint: check that
         starlette.responses.JSONResponse |
         starlette.responses.RedirectResponse is a valid Pydantic
         field type.`
    Pydantic can't build a response model from a Response | Response
    union. *Fix*: add `response_model=None` to the decorator and
    drop the return-type annotation:
        `@router.get("/github/start", response_model=None)`
        `async def github_start():`
    **Lesson**: any FastAPI endpoint that can return DIFFERENT
    Response subclasses on different code paths needs
    `response_model=None` — otherwise FastAPI tries to introspect the
    union as a Pydantic schema and crashes. This applies to any
    endpoint that mixes JSONResponse / RedirectResponse / Response
    in its branches.

70. **Orphan containers after `docker-compose.yml` rewrite.** Phase 1
    PR-1 deleted the `server` and `redis` services from
    `docker-compose.yml`. The OLD containers (`openvox-server`,
    `openvox-redis`) kept running — `docker compose up` doesn't
    touch containers whose service definitions no longer exist in
    the file. To clean up, an explicit one-time step is needed:
        `docker stop openvox-server openvox-redis`
        `docker rm   openvox-server openvox-redis`
        `docker volume rm openvox_redis-data`  # optional
    Any operator upgrading from a pre-Phase-1 install hits this.
    *Fix*: document the steps in the PR-6 README update (the
    Session 15 SESSION_LOG.md entry already captures the procedure).
    **Lesson**: when a service is removed from `docker-compose.yml`
    in a release, the changelog MUST include the manual cleanup
    step. Compose's design philosophy is "manage only what I'm
    told about" — removed services become invisible-but-running.
    Consider a `scripts/upgrade.sh` that automates the cleanup for
    operators.

71. **`wingetcreate.exe` is NOT preinstalled on `windows-latest`.**
    Older docs (and the original v0.1.2 Phase 4 PR-5 workflow
    comment) claim it ships with the GitHub Actions Windows runner
    image. It doesn't. The job fails with
        `The term 'wingetcreate.exe' is not recognized as a name of
         a cmdlet, function, script file, or executable program.`
    *Fix*: install via Microsoft's pinned redirect URL — this is
    also the canonical install path in their own README:
        `$wc = "$env:RUNNER_TEMP\wingetcreate.exe"`
        `Invoke-WebRequest -Uri https://aka.ms/wingetcreate/latest -OutFile $wc`
    **Lesson**: never trust "preinstalled on X runner" claims
    without verifying — runner images get pruned regularly.
    For wingetcreate specifically: don't use `dotnet tool install`
    either; see bug #72.

72. **`wingetcreate` is NOT a NuGet package.** Some Stack Overflow
    answers from 2022 suggest `dotnet tool install --global
    Microsoft.WingetCreate.CLI`. That fails with:
        `microsoft.wingetcreate.cli is not found in NuGet feeds
         https://api.nuget.org/v3/index.json, ...`
    *Fix*: use the .exe download from `aka.ms/wingetcreate/latest`
    (see bug #71). It's a standalone single-file binary, no
    runtime dependency.
    **Lesson**: distrust StackOverflow answers about Microsoft
    tooling that are >18 months old — Microsoft frequently changes
    distribution channels (NuGet → standalone, .NET → portable,
    etc.). Check the upstream repo's README before trusting tribal
    knowledge.

73. **`wingetcreate update` vs `submit`.** The `update` subcommand
    only works for packages that ALREADY exist in
    microsoft/winget-pkgs. For the FIRST submission of a new
    package, it fails with:
        `ERROR: repos/microsoft/winget-pkgs/contents/manifests/o/
         <Pkg>/<Name> was not found.`
    *Fix*: probe `https://api.github.com/repos/microsoft/winget-pkgs/
    contents/manifests/<letter>/<Owner>/<Name>` first. 404 → use
    `wingetcreate submit` against locally-rendered manifest templates
    (we keep these in `packaging/winget/*.yaml` with
    placeholders that PowerShell `-replace` substitutes at job
    runtime). 200 → use `update` as normal. The Phase 4 PR-5
    workflow now branches on this probe (PR #2 fix).
    **Lesson**: any release pipeline submitting to an upstream
    catalogue needs to handle the "first publish" case differently
    from "version bump". Treating them as a single code path is
    a latent bug that manifests only on the very first release.

74. **PyPI propagation race in chained release jobs.** When a
    later job in the same workflow does
        `pip install <package>==<just-uploaded-version>`,
    it loses a race against PyPI's index propagation ~80% of the
    time and fails with:
        `ERROR: Could not find a version that satisfies the
         requirement <pkg>==<ver> (from versions: <older versions>)`
    PyPI's upload endpoint and install index are
    eventually-consistent; sync takes 30 s — 2 min.
    *Fix*: poll `pip index versions <pkg>` until the new version
    appears before the install step. Pseudocode:
        `for i in $(seq 1 30); do`
        `  if pip index versions $pkg | grep -q $ver; then break; fi`
        `  sleep 10`
        `done`
    Hit this in the `publish-homebrew` job (which does
    `pip install openvox-core==<new>` to feed homebrew-pypi-poet).
    **Lesson**: never assume "upload succeeded" means
    "install will succeed" on PyPI. Build a wait-for-propagation
    step into any job that pip-installs from a freshly-uploaded
    version.

75. **Fine-grained PATs can't open cross-repo PRs.** GitHub
    fine-grained personal access tokens have a hard limitation:
    they cannot open pull requests from a fork to a repository
    OUTSIDE the resource owner's account. For wingetcreate's
    submission flow (PR from `<you>/winget-pkgs` to
    `microsoft/winget-pkgs`), the PAT is treated as not having
    permission on the destination repo, even if you'd give it
    every scope possible. The symptom: wingetcreate gets all the
    way through `Manifest validation succeeded: True` and
    `Submitting pull request for manifest...`, then dies with:
        `ERROR: Resource not accessible by personal access token`
    *Fix*: use a CLASSIC PAT with `public_repo` scope instead.
    Classic tokens don't have the cross-org PR restriction.
    Documented in microsoft/winget-create issues repeatedly.
    **Lesson**: fine-grained PATs are excellent for
    single-repo automation but blunt for cross-org PR submission
    flows. When a workflow targets a repo you don't own (e.g.
    upstream registries: winget-pkgs, homebrew-core, npm
    registry mirrors), reach for a classic PAT scoped to
    `public_repo`.

76. **PEP 594 removed `audioop` from stdlib in Python 3.13.**
    `packages/core/openvox/api/ws/twilio_stream.py:41` uses
    `import audioop` for μ-law / linear PCM conversion on Twilio
    Media Streams. On Python 3.13+ that import fails with:
        `ModuleNotFoundError: No module named 'audioop'`
    Symptom: `pip install openvox-core` into a 3.13+ venv builds
    fine, but the daemon crashes at startup when uvicorn imports
    the FastAPI app. With `KeepAlive: {SuccessfulExit: false}` in
    the launchd plist, the process respawn-loops until
    ThrottleInterval hits.
    *Fix*: conditional dep in `pyproject.toml`:
        `"audioop-lts>=0.2.1; python_version >= '3.13'",`
    `audioop-lts` vendors the removed stdlib module under the same
    name, so `import audioop` works unchanged. Pip skips it on
    older Pythons (stdlib has it) and installs on 3.13+.
    **Lesson**: PEP 594's deprecation list is long (audioop, aifc,
    cgi, asynchat, asyncore, imghdr, sndhdr, telnetlib, uu,
    xdrlib, nntplib, pipes, and more). Before any new release,
    grep imports for these and either rip them out or add their
    LTS shim packages as conditional deps. The PyPI ecosystem has
    `audioop-lts`, `legacy-cgi`, etc. for most of them.

77. **Phase 3 wizard saved keys; providers never read them.** The
    first-run wizard (`/api/v1/admin/setup/keys`) persists provider
    API keys into the encrypted SQLite-backed store
    (`packages/core/openvox/secrets.py`). Every provider module
    (BytePlus TTS/STT/LLM, OpenAI, ElevenLabs, etc.) reads its key
    from `get_settings().<provider>_api_key`, which pydantic-settings
    hydrates from env vars / `.env`. The encrypted store and the
    settings layer were never bridged. Symptom: a user enters keys
    via the wizard, clicks "Save", sees success, then every feature
    that uses those keys errors with "API_KEY not set" — including
    the Test-voice button, the Build-by-voice setup chat, every
    agent's actual conversation flow. The hybrid resolver at
    `secrets.py:223` (`resolve_provider_key()`) was scaffolded for
    this but never called.
    *Fix*: at app startup, copy stored keys into `os.environ` (env
    wins, falls back to store), then bust `get_settings()`'s
    lru_cache so providers re-read fresh. Helper:
    `_hydrate_secrets_into_env()` in `api/app.py`.
    **Lesson**: an encrypted-store feature that doesn't wire into
    the actual consumers is worse than no feature — it presents a
    UX of "your keys are saved" while actually being a no-op.
    Any "secrets feature" change needs an end-user smoke test
    that verifies a provider call works AFTER wizard-only key
    entry, NOT just that the keys appear in SQLite.

78. **Provider registration runs BEFORE secret hydration.** Even
    after wiring the encrypted store to env vars at startup, the
    providers had cached the empty values. Reason:
    `register_builtins()` in lifespan calls `BytePlusTTS()` which
    does `self._api_key = get_settings().byteplus_voice_api_key`
    in `__init__` — a one-shot snapshot. If hydration runs AFTER
    register_builtins, the cached `_api_key` stays empty forever.
    *Fix*: reorder lifespan so the order is `init_db()` →
    `_hydrate_secrets_into_env()` → `register_builtins()`. Comment
    the ordering explicitly so a future refactor doesn't break it.
    **Lesson**: any startup-time configuration that providers cache
    in `__init__` is a load-bearing ordering constraint. Either
    have providers re-read on each call (slower, but immune) or
    document the ordering loudly in the lifespan function.

79. **Next.js static export needs `trailingSlash: true` to be
    served by plain static-file mounts.** Default `output: 'export'`
    produces flat files: `dashboard.html` lives at the SAME level
    as the `dashboard/` directory holding sub-pages. FastAPI's
    StaticFiles (and most static-file servers) look for
    `dashboard/index.html` when serving `/dashboard/` — that file
    doesn't exist in the default export, so the request 404s.
    *Fix*: `trailingSlash: process.env.BUILD_OUTPUT === "export"`
    in `next.config.mjs`. With this, Next.js writes
    `dashboard/index.html`, `dashboard/agents/index.html`, etc. —
    the index.html-per-directory convention.
    **Lesson**: anytime you point a static-file server at a
    Next.js export, set `trailingSlash: true`. The default mode
    is for SPAs that have their own routing layer.

80. **Mounting StaticFiles at the wrong level produces "shows
    wrong page" + "no CSS" simultaneously.** First instinct was
    `app.mount("/dashboard", StaticFiles(directory=out/))`. That
    produces two compounding problems:
      a. /dashboard/ falls back to `out/index.html` (the marketing
         landing page from app/page.tsx), not the dashboard
         (which lives at `out/dashboard.html` or
         `out/dashboard/index.html`).
      b. Next.js's asset URLs are `/_next/static/...` — root-
         relative. The StaticFiles mount at `/dashboard` only
         serves paths under `/dashboard/_next/...`, so every CSS
         and JS file 404s and the page renders as unstyled HTML.
    *Fix*: two separate mounts —
      `app.mount("/_next", StaticFiles(directory=out/_next/))` for assets
      `app.mount("/dashboard", StaticFiles(directory=out/dashboard/, html=True))` for pages
    Plus an explicit `@app.get("/")` that returns
    `FileResponse(out/index.html)` so the landing page is served
    at the root.
    **Lesson**: a Next.js static export expects to be served at a
    URL prefix that matches its `basePath`/`assetPrefix` config.
    When serving it via a backend's static-file mount, model the
    URL-prefix-to-directory-prefix relationship carefully — one
    mount usually isn't enough.

81. **Conflicting root handlers silently shadow each other.**
    `packages/core/openvox/api/routes/health.py:13` had a
    `@router.get("/")` returning hardcoded service-info JSON. Any
    later `@app.get("/")` (e.g. the landing-page route from #80)
    is shadowed because FastAPI's router matches in registration
    order. The user typed `localhost:8000/` expecting the dashboard
    landing page and got the JSON blob instead, even though the
    new handler was correctly registered.
    *Fix*: deleted the redundant root handler from health.py.
    `/health` already covers health-check use cases; `/docs` is
    auto-served by FastAPI; the service-info field was unused by
    anything in the codebase.
    **Lesson**: never register `@router.get("/")` in a sub-router
    "just for niceness". The root path is shared real estate; any
    feature that wants to mount something there will silently
    fail. If you really need service-info, put it at
    `/api/v1/info` or similar.

82. **"Phase done" requires a human click-through, not a
    `curl /health`.** I marked Phase 4 (the entire "OpenVox
    without Docker" effort) as complete when the daemon
    successfully stayed running. The user's actual end-user
    smoke test surfaced bugs #77–81 within five minutes — none of
    which would have been caught by any API-level test.
    *Lesson*: for any feature whose value is "a user can open the
    dashboard and use it", validation MUST include a real browser
    click-through of the most-common flow before claiming done.
    Specifically for the install-via-pipx path: install on a
    clean machine, start the daemon, open the dashboard in a
    browser, walk the first-run wizard, then USE the configured
    agent at least once. If any step errors or shows a broken
    UI, the phase isn't done. The cost of running the smoke
    test is 5 minutes; the cost of skipping it and shipping a
    broken release is multiple version-burnt PyPI uploads and
    a frustrated user.

83. **A Python wheel is NOT a WinGet "portable zip" installer.**
    Phase 4 PR-4 designed the WinGet manifests to point at the
    `.whl` URL with `InstallerType: zip` +
    `NestedInstallerType: portable` + `RelativeFilePath: Scripts\
    openvox.exe`. The wheel IS a zip, so the upload validated.
    But on actual install, Microsoft's bot extracted the zip and
    failed with:
        `APPINSTALLER_CLI_ERROR_NESTEDINSTALLER_NOT_FOUND`
        `Unable to locate nested installer at ...\Scripts\openvox.exe`
    Reason: pip's `openvox.exe` shim is generated AT INSTALL TIME
    by the `console_scripts` entry-point machinery. The wheel is
    pure platform-agnostic source — there's no Windows .exe inside
    to extract. WinGet's portable installer doesn't run pip, so
    the .exe is never created.
    *Fix*: there isn't a workable wheel-as-WinGet path. The two
    real options are:
      a) PyInstaller-package a self-contained `openvox.exe` (~50-100
         MB) that bundles Python + all deps; ship THAT as the
         WinGet artefact. Adds a separate CI step + Windows runner.
         Triggers SmartScreen warnings without a code-signing cert
         ($300-400/yr).
      b) Tell Windows users to `pip install openvox-core` from
         PowerShell — pip generates openvox.exe correctly. This
         is what `docs/install.md` says today.
    The Phase 4 PR-4 WinGet manifests + the workflow's
    publish-winget job are kept in the repo but the job is
    gated on a `PYINSTALLER_BUILT` actions variable that doesn't
    exist anywhere — effectively a kill-switch until option (a)
    is implemented.
    **Lesson**: before designing a packaging path for a foreign
    package manager (WinGet, Homebrew core, apt, etc.), verify
    the package manager's installer types ACTUALLY match what
    your source artefact contains. WinGet portable expects an
    .exe; Homebrew formula resources expect sdist URLs; apt
    expects .deb. The shape of your artefact must match BEFORE
    writing manifests, not after.

84. **`monkeypatch` doesn't track direct `os.environ` mutations.**
    pytest's monkeypatch reverts the env vars IT sets via
    `monkeypatch.setenv`. If the code under test mutates
    `os.environ` directly (e.g. `_hydrate_secrets_into_env` setting
    `BYTEPLUS_VOICE_API_KEY = ...`), those values LEAK into the
    next test. The bleed shows as bizarre cross-test failures
    where test B sees env vars test A set, often only when the
    suite is run in a specific order.
    *Fix*: in conftest fixtures, snapshot `dict(os.environ)` at
    entry and restore it byte-for-byte at exit. See
    `packages/core/tests/conftest.py::tmp_openvox_home`.
    **Lesson**: any fixture that yields to code which might
    `os.environ[...] = ...` directly needs an env snapshot,
    not just monkeypatch.

85. **Module-level engine cache leaks across tests.**
    `openvox.db.session._engine` is a process-level singleton
    constructed on first `get_engine()` call. It binds to
    `settings.database_url` at that moment. Subsequent tests
    with different `DATABASE_URL` env vars STILL get the first
    test's engine + connection pool, pointing at the wrong DB.
    *Fix*: bust the engine + sessionmaker in the conftest
    cache-bust helper:
        ```
        import openvox.db.session as session_mod
        session_mod._engine = None
        session_mod._sessionmaker = None
        ```
    **Lesson**: every module-level cache that closes over
    settings is a cross-test-leak waiting to happen. When you
    add one, also add it to `tests/conftest.py::_bust_caches`.

86. **Host env vars silently leak into tests.**
    A contributor with `BYTEPLUS_VOICE_API_KEY=...` in their
    shell profile sees tests that should fail (testing the "no
    keys configured" path) pass spuriously. The same tests fail
    in CI where the env is clean. Classic "works on my machine."
    *Fix*: in `tmp_openvox_home`, explicitly `monkeypatch.delenv`
    every provider-key env var on entry. Keep the list in sync
    with the mapping in `_hydrate_secrets_into_env`.
    **Lesson**: never assume the test process's env is clean.
    Either snapshot+restore (#84) or explicit delenv on entry
    for every var the test logic depends on.

87. **uvicorn's `dictConfig` wipes module-level logger handlers.**
    `uvicorn.run(app, log_level="info")` calls
    `logging.config.dictConfig(...)` at startup. The default
    dict only configures `uvicorn.*` loggers, but the dictConfig
    operation removes handlers from non-uvicorn loggers that had
    been configured earlier via `logging.basicConfig`. So
    `logger.info()` from `openvox.api.app` produces no output
    in the daemon's stderr — surfaced when trying to verify
    "did hydration run?" from `openvox logs`.
    *Fix*: pass a custom `log_config` dict to `uvicorn.run`
    that preserves uvicorn defaults AND adds an "openvox"
    logger entry with handler:
        ```
        "loggers": {
            "uvicorn": {...},
            "uvicorn.error": {...},
            "uvicorn.access": {...},
            "openvox": {
                "handlers": ["default"], "level": "INFO",
                "propagate": False,
            },
        }
        ```
    See `packages/core/openvox/cli/commands/run.py`.
    **Lesson**: ANY module's logger.info() / logger.warning()
    that you want visible in production logs needs an explicit
    entry in the uvicorn log_config — `disable_existing_loggers=
    False` is necessary but NOT sufficient.

88. **Subprocess stderr buffer flushes only on process exit.**
    Reading a subprocess's stderr log file while the subprocess
    is STILL RUNNING shows whatever has been flushed to the OS
    so far — buffered writes are still in the subprocess's
    libc stdio buffer. The `logger.info()` call has happened,
    but its bytes haven't crossed the kernel boundary yet.
    *Fix*: `proc.terminate()` + `proc.wait()` BEFORE reading
    the log file. The buffer flushes on graceful shutdown.
    **Lesson**: any test that asserts on subprocess output
    must stop the subprocess first. Mid-run reads are unreliable
    for anything except polling /health-style probes (which
    don't depend on log output).

89. **Shared `'a'`-mode log file across multiple subprocesses
    has confusing file-handle inheritance behaviour.**
    `subprocess.Popen(stderr=open(path, "a"))` opens a NEW file
    handle in the parent. Each subprocess inherits a DIFFERENT
    FD pointing at the same file. When the parent (test process)
    reads the file path AFTER the subprocesses stop, what it
    sees depends on OS-specific append-mode semantics + which
    FD flushed last. We saw the second daemon's log entries
    silently missing from a shared `daemon.stderr.log`.
    *Fix*: give each subprocess its own log file. Either
    `daemon.d1.stderr.log` / `daemon.d2.stderr.log` via a `label`
    parameter, or `tempfile.NamedTemporaryFile`. Truncate-mode
    (`"w"`) is fine when each subprocess has its own file.
    **Lesson**: do NOT share log files across subprocesses
    with `'a'` mode. The file-system layer makes it look like
    it works; sometimes it doesn't.

90. **Hatch doesn't ship sibling files alongside the package.**
    `[tool.hatch.build.targets.wheel] packages = ["openvox"]`
    only includes files UNDER `openvox/`. If you have project-
    level config files (`alembic.ini`) or scripts directories
    (`alembic/`) at the SAME level as `openvox/`, they don't
    end up in the wheel. The runtime daemon then fails on a
    fresh `pip install` because the file it needs at runtime
    isn't there.
    *Fix*: explicit `[tool.hatch.build.targets.wheel.force-
    include]` block listing each sibling path:
        ```
        [tool.hatch.build.targets.wheel.force-include]
        "alembic" = "alembic"
        "alembic.ini" = "alembic.ini"
        ```
    Also add to `[tool.hatch.build.targets.sdist].include` for
    source-installs to work. Verify by `python -m zipfile -l`
    on the built wheel before publishing.
    **Lesson**: any file the runtime needs that isn't a .py
    inside the package directory needs explicit force-include.
    Test by building the wheel + listing its contents BEFORE
    you publish.

91. **Alembic `autogenerate` defaults silently miss schema
    changes.**
    Out of the box, `alembic revision --autogenerate` doesn't
    compare column TYPES or server-side DEFAULT clauses against
    the model definitions. A change like `VARCHAR(50)` →
    `VARCHAR(100)` or `DEFAULT 'silero'` → `DEFAULT 'none'`
    is silently ignored. autogenerate produces an empty
    migration; the model and the DB schema drift apart.
    *Fix*: in `alembic/env.py::do_run_migrations`:
        ```
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        ```
    Both default `False`; both should be `True` for any project
    that takes its schema seriously.
    **Lesson**: Alembic's autogenerate is a power tool with
    safety knobs OFF by default. Turn them on at project setup
    time so you don't discover the misalignment 6 months in.

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
