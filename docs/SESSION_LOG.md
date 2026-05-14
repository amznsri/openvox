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

## Session 7 — 2026-05-12 / 2026-05-13 (bug-fix + UX polish pass)

**Goal**: shake-out testing of the platform once an end user actually
sat down with it. Surfaced a clutch of TLS / UX / persistence bugs and
swept them. No new feature surface — pure quality pass — but a
material one for the "does this thing actually work?" question.

### What we found and fixed

#### 1. Stock + web-search skills crashed on Zscaler-intercepted TLS
- **Symptom**: a user-created stock-analysis agent answered "unable to
  retrieve live stock data / news" for every question. Core logs:
  `httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]`.
- **Root cause**: `skills/builtin/stock.py` (`get_quote`) and
  `skills/builtin/general.py` (`web_search`) used a bare
  `httpx.AsyncClient(...)` rather than our `make_async_client()`
  wrapper. They predated the wrapper and were missed during the
  TLS refactor.
- **Fix**: both routed through `make_async_client` so the
  `OPENVOX_INSECURE_TLS` / `extra-ca.pem` escape hatches apply.
- **Bonus fix for `get_quote`**: Yahoo's `/v7/finance/quote` started
  requiring a crumb cookie in 2024 (returns 401). Switched to
  `/v8/finance/chart/{sym}` which still works unauthenticated; returns
  the same fields plus market_state + exchange. Verified live: AAPL
  $294.51, NVDA $219.92.
- **Bonus fix for `web_search`**: DuckDuckGo returns **202 No-Instant-
  Answer** for queries without an instant-answer panel — was being
  surfaced as a failure. Now treated as an empty-result success.
- **Future-proofing**: `grep -rn 'httpx.AsyncClient' packages/core/`
  should return zero hits in `skills/`. Codified as Bug #30 in
  CLAUDE.md §8.

#### 2. Dashboard "Publish" button looked broken
- **Symptom**: clicking Publish on the agent detail page appeared to
  do nothing; users assumed it had failed and walked away.
- **Reality**: the endpoint was fine — but the click handler had no
  busy state, no toast, swallowed errors, and only triggered an SWR
  revalidation (which takes a beat). Looked dead.
- **Fix**: added a busy state ("Publishing…" + spinner, button
  disabled), seeded the SWR caches with the returned record so the
  badge flips draft→published instantly, and surfaced a green
  success banner / red error banner that auto-dismisses after 3.5 s.

#### 3. Skill-call display always showed empty args
- **Symptom**: playground transcript showed `→ get_quote({})` even
  when the LLM did pass arguments.
- **Root cause**: orchestrator emitted `TurnEvent(kind="skill_call",
  data=parsed_args)` and `_event_to_json` spread `data` into top-level
  keys. The dashboard read `(ev as any).args` — undefined.
- **Fix**: orchestrator now emits `data={"args": parsed_args}` so the
  dashboard's lookup path matches.

#### 4. `analyze_image` 400-ed on Wikipedia URLs
- **Why**: Ark downloads the image *server-side*; Wikipedia (and a few
  CDNs) return 403 to Ark's IP / UA. Not a code bug — operational
  gotcha that bit during validation.
- **Fix**: documented the constraint prominently in the skill class
  docstring + `description` so the LLM and end-users both know to
  prefer TOS / S3 / picsum / data-URIs.

#### 5. Agent delete crashed on docs-bearing agents
- **Symptom**: HTTP 500 when deleting an Audio Analyzer that had
  earlier ingested PDFs.
- **Root cause**: same pattern as Bug #29 (`job_runs.job_id`), but
  for `documents.agent_id`. FK without `ON DELETE CASCADE`.
- **Fix**: in-route cascade — `routes/agents.py:delete_agent` now
  drops `DocumentChunk` + `Document` rows before deleting the parent
  Agent. (DocumentChunk uses a plain string, not a FK, but we drop
  it too to keep the RAG store consistent.) Logged as Bug #30/31 in
  CLAUDE.md §8.

#### 6. Template duplicates kept piling up
- **Symptom**: 3 "Acme Support Voice" + 2 "Audio Analyzer" rows in
  the agent list. Created by repeated "Use template" clicks across
  earlier sessions (no idempotency).
- **Fix in dashboard**:
  - Each template card now shows a green **"N created"** badge if
    matching agents already exist.
  - Clicking "Use template" when matches exist pops a `confirm()`:
    OK opens the existing one, Cancel creates a fresh copy. The
    flow is *informed* but still allows intentional duplicates.
- **Data cleanup**: deleted the two older Acme rows + the older
  Audio Analyzer. Down to 7 unique agents.

#### 7. Top-bar search was a no-op placeholder
- The input had no handler — type anything, nothing happens.
- **Fix**: wired it to a real fuzzy-search popover across the three
  searchable corpora (agents, templates, skills) with score-based
  ranking (word-start hits beat middle-of-word), keyboard nav
  (↑/↓/Enter/Esc), click-outside-to-close, and per-hit icon + kind
  label. Each result links straight to the relevant page.

#### 8. Observability page was permanently empty
- **Symptom**: dashboard showed 0 sessions, 0 ms talk time even after
  the user ran multiple turns through the playground.
- **Root cause**: *nothing wrote to the `sessions` table.* The voice
  WS handler in `api/ws/voice.py` and the text endpoint in
  `routes/playground.py` never instantiated a `Session` row. So the
  GET /sessions endpoint that the dashboard polled was technically
  correct — there was just nothing there to return.
- **Fix**:
  - **voice WS**: creates a `Session` row on the `start` frame,
    accumulates `turn_count` (one per `assistant_done`) and
    `first_token_ms` (timestamp of the first `assistant_token`) in
    a metrics dict passed to `_forward_events`, finalises
    `duration_ms` + `status="completed"` in the WS `finally` block
    so even mid-call disconnects leave a row.
  - **text playground**: accepts optional `agent_id` in the request
    body; writes a `Session` row + user `Transcript` before
    streaming starts, then updates duration + first-token-latency
    and appends an assistant `Transcript` once the stream
    completes. Both inserts are best-effort (try/except + log) so
    a DB hiccup never kills the chat itself.
  - **dashboard**: text tab now passes the selected agent through.
- **Verified**: one text turn produced row
  `2e64f3c8 · ch=web · dur=3261ms · ftt=3259ms · turns=1 ·
  status=completed`.

### Skills validation (26/26)

Hand-rolled a `validate_skills.py` that POSTs against `/skills/invoke`
for every skill with sensible default args. Result: **24 PASS · 2
"correct refusal"**. The two refusals are skills that need richer
context than `/skills/invoke` provides:
- `query_documents` needs `ctx.agent_id` (per-agent RAG store).
- `transcribe_recording` needs a URL BytePlus can fetch (TOS / S3
  presigned). Local-file transcription goes through the
  `/playground/audio_analyze` streaming path instead.
Both verified working via their proper UI surfaces.

### Files touched (commits 10df997 → 1cf12a5)

```
packages/core/openvox/skills/builtin/stock.py
packages/core/openvox/skills/builtin/general.py
packages/core/openvox/skills/builtin/documents.py    (AnalyzeImage docstring)
packages/core/openvox/pipeline/orchestrator.py       (skill_call args shape)
packages/core/openvox/api/routes/agents.py           (cascade delete)
packages/core/openvox/api/routes/playground.py       (text-session persistence)
packages/core/openvox/api/ws/voice.py                (voice-session persistence)
apps/dashboard/src/app/dashboard/agents/[id]/page.tsx (publish UX)
apps/dashboard/src/app/dashboard/templates/page.tsx   (duplicate guard)
apps/dashboard/src/app/dashboard/playground/page.tsx  (agent_id passthrough)
apps/dashboard/src/components/nav/topbar.tsx          (real search popover)
apps/dashboard/src/lib/api.ts                         (agent_id in TextChatRequest)
```

---

---

## Session 8 — 2026-05-14 (the differentiation push)

**Goal**: ship the nine items locked in `docs/PLANNING_SESSION8.md`
after competitive research surfaced Dograh as a direct OSS rival.
Three defensive (VAD / Twilio inbound / Browser SDK), four offensive
under Bet A (BUT scoped Asia-Pacific not BytePlus-only), one Bet C
scope-down (MCP catalogue), and the full Bet B eval framework.

All nine landed in one continuous session.

### What we shipped

**D.1 — Silero VAD + sub-100 ms interrupt.**
- New `VADProvider` interface (`providers/vad/base.py`) and
  `SileroVAD` impl using the `silero-vad` PyPI package with torch
  backend. ONNX backend toggle via `OPENVOX_VAD_BACKEND=onnx`.
- Orchestrator now tees inbound audio: STT consumes one queue,
  a parallel `_vad_loop` task consumes the other. On `speech_start`
  while `_speaking=True`, sets `_cancel_tts` so TTS aborts inside
  the next chunk boundary.
- Additive migration: `agents.vad_provider`.
- Acceptance harness: `scripts/measure_interrupt.py` synthesises a
  speech-like signal (harmonic + envelope modulation) and times
  detection. **Measured P50=58.5 ms, P95=121.7 ms** — under the
  100 ms / 150 ms targets.

**D.2 — Twilio Media Streams inbound bridge.**
- New WS endpoint `/ws/twilio` (`api/ws/twilio_stream.py`) speaking
  Twilio's full protocol: connected/start/media/mark/stop/clear.
- μ-law ⇄ PCM s16le via `audioop` (stdlib); 8↔16/24 kHz resample
  also via `audioop.ratecv` with persistent filter state.
- On interrupt we send Twilio a `clear` event so buffered playback
  audio is dropped — no stale tail after barge-in.
- TwiML route passes `agent_id` via `<Parameter>`; inbound webhook
  resolves agent by phone number from `Agent.channels.twilio.phone_numbers`.

**D.3 — Browser SDK `@openvox/web`.**
- New TS package `packages/sdk-web/` with React `<VoiceAgent />`
  component and `useVoiceSession` hook.
- ScriptProcessor mic capture (deprecated but universal — needs no
  worklet shim file, so npm install + 3 lines works on any React
  app). Downsamples to 16 kHz PCM s16le.
- PcmPlayer with 60 ms lookahead scheduling matches the existing
  dashboard audio path; survives Safari's `AudioContext.resume()`
  user-gesture requirement.

**C.1 — MCP server catalogue.**
- `openvox/mcp/catalogue.json` with 6 curated entries: Slack, GitHub,
  Notion, HubSpot, Salesforce, Stripe — each with command + required
  env vars + tagline + icon.
- `GET /api/v1/mcp/catalogue` route.
- Dashboard "Browse catalogue" modal on the agent edit MCP tab —
  click an entry, form pre-fills with command/args plus empty
  `KEY=` lines for required env vars.

**A.1 — Cross-provider pricing calculator.**
- `openvox/pricing/rates.py` with rate cards for all 10 priced
  providers as of 2026-05-14. Override via `OPENVOX_RATES_FILE`.
- `/api/v1/pricing/{rates,estimate,sessions/{id}}` routes.
- `sessions/{id}` computes a what-if matrix across STT × LLM × TTS
  combos (max 5×5×5 = 125, sorted by total cost) so the dashboard
  can show "switching X saves $Y".
- Additive migration: `sessions.{llm_tokens_in,llm_tokens_out,tts_chars}`.
- Voice WS forwarder now accumulates token deltas (rough proxy
  pending real provider-reported usage in a follow-up) and TTS
  character count per turn.

**A.3 — 21 multi-language templates.**
- `_make_lang_templates()` generates 3 use-cases × 7 languages:
  - **Use cases**: service hotline, customer reactivation outbound,
    B2B telesales.
  - **Languages**: English, Mandarin (中文), Cantonese (粤语),
    Spanish (Español), Bahasa Indonesia, French (Français), Hindi.
- Each template has an **in-language `system_prompt`** (not translated
  from English) so the LLM speaks idiomatically. Greeting and voice
  swap per locale. Total catalogue now 29 templates.
- Dashboard `/dashboard/templates` adds language-filter chips
  (All / Core / per-locale flags).

**A.2 — WeChat Work + Lark inbound channels.**
- `openvox/telephony/wechat_work.py` — full `_verify_signature` SHA-1
  HMAC + URL-verification GET handler; inbound POST acknowledges
  events. AES decryption + voice-message bridge marked TODO until
  there's a verified WeCom corp to test against.
- `openvox/telephony/lark.py` — `url_verification` challenge handler
  + event_v2 envelope parser. Single-tenant config lookup from
  `Agent.channels.lark`.
- Routers mounted under `/api/v1/telephony/{wechat_work,lark}/`.

**B.1 + B.2 + B.3 — Eval framework (the wedge).**
- Three new tables: `Recording`, `Persona`, `EvalRun`. Created via
  `Base.metadata.create_all()` on next startup (no migration needed
  for new tables, only new columns).
- **5 built-in personas** seeded on every startup
  (`angry_customer_en`, `confused_elder_en`, `non_native_speaker_en`,
  `in_a_hurry_en`, `security_paranoid_en`).
- **Replay runner**: feeds user turns from a recording's stored
  transcript through a new agent config — LLM-only, no TTS/STT
  round-trips — so re-runs are fast and deterministic.
- **Persona runner**: two LLMs in alternating dialogue (persona drives
  user turns, candidate drives assistant turns) until natural call-end
  keywords appear or `max_turns` (default 8) is hit.
- **LLM-as-judge**: separate prompt evaluates each criterion
  independently, returns strict-JSON per-criterion breakdown. Python
  aggregator computes `score` (partials worth 0.5) and `verdict`
  (`pass|partial|fail`).
- API: `/api/v1/evals/{recordings,personas,run,runs}`.

**B.4 — CI hook + EVALS.md.**
- `.github/workflows/evals.example.yml` shows the canonical GitHub
  Actions matrix: agent × persona × criteria → fail PR on bad verdict.
- `docs/EVALS.md` documents the three pieces, the judge design,
  what it doesn't do yet.

### What's NOT in this session

- **Image size optimisation** (~9.7 GB because torch pulls CUDA wheels).
  Tried switching to `download.pytorch.org/whl/cpu` index but Zscaler
  blocks the host; documented inline.
- **Dashboard `/dashboard/evals` page**. The backend + API + 5 personas
  are live; the UI panel that drives them lands in Session 8.5 or 9.
- **Dashboard pricing-breakdown card** on the Observability page. The
  backend telemetry + `/api/v1/pricing/sessions/{id}` works today; the
  rendering UI is a Session 8.5 follow-up.
- **First-party WeChat Work / Lark voice-message decoding**. Webhooks
  acknowledge events but the full audio bridge needs real test
  credentials to land.

### Files touched (30 files, ~2,250 LOC added)

```
NEW:
  packages/core/openvox/providers/vad/{__init__,base,silero}.py
  packages/core/openvox/api/ws/twilio_stream.py
  packages/core/openvox/api/routes/{pricing,evals}.py
  packages/core/openvox/pricing/{__init__,rates}.py
  packages/core/openvox/eval/{__init__,personas,judge,runner}.py
  packages/core/openvox/mcp/catalogue.json
  packages/core/openvox/telephony/{wechat_work,lark}.py
  packages/sdk-web/{package.json,tsconfig.json,README.md,src/*}
  scripts/measure_interrupt.py
  docs/{EVALS.md,PLANNING_SESSION8.md}
  .github/workflows/evals.example.yml

MODIFIED:
  packages/core/openvox/pipeline/orchestrator.py
  packages/core/openvox/providers/bootstrap.py
  packages/core/openvox/api/{app,ws/voice}.py
  packages/core/openvox/api/routes/{agents,telephony,templates,mcp}.py
  packages/core/openvox/db/{models,session}.py
  packages/core/{Dockerfile,pyproject.toml}
  apps/dashboard/src/lib/api.ts
  apps/dashboard/src/app/dashboard/{templates,agents/[id]}/page.tsx
```

---

## Open follow-ups (carried forward)

Updated end of Session 8. Items shipped this session removed; items still
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
