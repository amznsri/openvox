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

### Session 8 post-merge fix-ups

After the main commit, smoke-testing the playground UI surfaced two
deploy-discipline issues worth capturing here so future sessions don't
re-step on the rake:

- **Dashboard MCP catalogue button was invisible** — the source had it,
  but we only rebuilt the `core` container during Session 8. The
  Next.js production bundle in `dashboard` was stale and didn't include
  the new "Browse catalogue" button on the Agent → MCP tab. Fix:
  rebuild + restart the dashboard container too. Logged as CLAUDE.md §8
  bug #40 with the canonical "TSX change → rebuild dashboard" rule.
- **`docker cp` nesting trap** — when iterating quickly we used
  `docker cp packages/core/openvox openvox-core:/app/openvox` to push
  new Python source into the running container without a rebuild. With
  `/app/openvox` already present, that copies *into* it rather than
  overwriting, so the container loaded the old `bootstrap.py` from
  `/app/openvox/` while the new one sat unused at
  `/app/openvox/openvox/`. Took us ~5 minutes to diagnose. Use the
  trailing-dot form (`packages/core/openvox/.`) when overwriting an
  existing directory. CLAUDE.md §8 #41.

Other Session 8 lessons captured in §8: silero-vad's hardcoded
`onnx=True` flag (#42), Docker Hub registry-mirror flakes (#43),
PyTorch CPU-index blocked by Zscaler (#44).

---

## Session 9 — 2026-05-18 (closing the Session-8 backlog)

**Goal**: ship the seven priority items carried forward from Session 8
(eval framework UI, pricing card, image diet, real LLM token usage,
WeChat/Lark audio bridges, scheduler webhook, skill hot-reload), plus
verify Session 8 features end-to-end against the running stack.

### What shipped

Five of seven priority items closed:
- **#6 Scheduler webhook trigger** (`a3b9a63`): `trigger_type="webhook"`,
  `POST /api/v1/jobs/webhook/{token}` route with token-based auth,
  dashboard `WebhookUrlCallout` with copy button. Optional JSON body
  merged into payload for that single run; disabled / wrong-token
  cases return 200 with `received:false` to prevent enumeration.
- **#7 Skill hot-reload** (`a3b9a63`): `watchfiles>=0.24.0` watching
  `~/.openvox/skills/` (or `OPENVOX_SKILLS_DIR` override), wired
  into the FastAPI lifespan.
- **#4 Real provider-reported LLM token usage** (`384e462`):
  `LLMResponseChunk.usage` + `stream_options.include_usage=true`
  on every OpenAI-compat client. Orchestrator emits a new
  `llm_usage` TurnEvent kind. WS forwarder + text playground track
  both word-count `_approx` (always populated) and provider
  `_real` (when emitted); final write prefers `_real` when > 0.
- **#2 Pricing-breakdown card on Observability** (`384e462`): clickable
  session rows → slide-in drawer with stacked-bar component cost
  + what-if matrix + "switch to X to save $Y" recommendation.
- **#1 Evals dashboard page** (`384e462`): full UI over the eval
  framework backend — stats row, recent-runs table, detail drawer
  with per-criterion judge breakdown + transcript, RunEvalModal
  for new runs. Observability drawer gains "Save as recording".
  Sidebar gets the Evals link.

Also caught + fixed two correctness bugs surfaced during code review
(commit `d04f85b`): stale `"doubao-seed-1.6-250615"` default in three
sites, unbounded recursion in `_llm_turn`. Both logged as CLAUDE.md
§8 #45 and #46.

Plus a Session-9-kickoff `2e0fc7a` shipped the **ngrok sidecar
service + complete Telegram pipeline** (webhook handler with
secret-token verification, voice + text inbound, voice + text reply,
ffmpeg PCM→OGG-Opus encoding, dashboard "Connect Telegram" wizard
with @BotFather deep-link). Voice/text hybrid pre-bakes the
infrastructure Session 10 will need.

### Deferred (three items, all external-dependency-gated)

1. **Image-size diet** — PyTorch CPU mirror Zscaler-blocked here.
   Retry from unrestricted egress.
2. **WeChat Work / Lark audio bridges** — needs verified test
   credentials (WeCom EncodingAESKey + Lark tenant_access_token).
3. **Telegram E2E test** — Docker daemon was down when the rest
   of Session 9 shipped; pipeline code complete.

### End-of-session verification pass

After Docker came back up: rebuilt core + dashboard, verified each
of the five shipped items via curl + dashboard click-through. Found
+ fixed two telemetry leaks in the process (commit `1bf4a3e`):

- Dockerfile set `OPENVOX_DATA_DIR=/data` but pydantic-settings
  reads `DATA_DIR`. The `/data` volume mount was silently ignored
  → settings.data_dir resolved to `./.openvox` inside `/app`.
  Renamed Dockerfile ENV to match settings field name.
- `/api/v1/sessions` serialisers didn't expose the new pricing
  telemetry columns (llm_tokens_in/out, tts_chars). Columns were
  being WRITTEN correctly but dashboard list view couldn't see
  them. Added to `_session_to_dict`.

### Files touched

```
packages/core/openvox/scheduler/engine.py        (webhook trigger_type)
packages/core/openvox/api/routes/jobs.py         (webhook fire route)
packages/core/openvox/skills/registry.py         (reload_local())
packages/core/openvox/skills/watcher.py          (new — async file watcher)
packages/core/openvox/providers/base.py          (LLMResponseChunk.usage)
packages/core/openvox/providers/byteplus/llm.py  (stream_options)
packages/core/openvox/providers/openai_compat/_openai_base.py (same)
packages/core/openvox/api/routes/pricing.py      (new)
packages/core/openvox/api/routes/evals.py        (already existed; now used)
packages/core/openvox/api/routes/sessions.py     (new telemetry columns)
apps/dashboard/src/app/dashboard/evals/page.tsx  (new)
apps/dashboard/src/app/dashboard/observability/page.tsx (pricing drawer)
apps/dashboard/src/app/dashboard/schedules/page.tsx (WebhookUrlCallout)
apps/dashboard/src/components/nav/sidebar.tsx    (Evals link)
apps/dashboard/src/lib/api.ts                    (pricing + eval helpers)
docker-compose.yml                                (ngrok sidecar)
.env.example                                      (NGROK_AUTHTOKEN)
packages/core/openvox/api/routes/telephony.py    (Telegram pipeline)
packages/core/openvox/telephony/telegram.py      (new — Bot API wrapper)
apps/dashboard/src/components/setup/             (Telegram wizard)
```

Commits: `8d02382` (plan) → `2e0fc7a` (Telegram kickoff) → `d04f85b`
(correctness fixes) → `a3b9a63` (#6 + #7) → `384e462` (#4 + #2 + #1)
→ `1bf4a3e` (verification fixes) → `8238b3a` (memory refresh).

---

## Session 10 — 2026-05-18 (voice-driven Setup Assistant)

**Goal**: build the headline differentiation feature — non-technical
users create voice agents *by talking to a voice agent*.

### What shipped

Single commit `71f47d2` (~1300 LOC). End-to-end verified with four
real LLM turns against the live BytePlus Ark stack — the agent
correctly classified the use-case, instantiated the right template,
named the agent, set its greeting verbatim, described remaining
manual setup, and published.

**Backend**:
- `skills/builtin/setup.py` (new) — six skills:
  `list_templates`, `recommend_template` (keyword classifier, not
  another LLM round), `instantiate_template`, `update_agent_field`
  (with hard-coded allow-list), `publish_agent`,
  `describe_remaining_setup`.
- `api/routes/templates.py` — added `setup-assistant` built-in
  template + `GET /api/v1/templates/setup-assistant/singleton`
  (get-or-create, idempotent so the agents page doesn't accumulate
  one SA entry per voice-setup click).
- `api/routes/agents.py` — new `POST /api/v1/agents/{id}/turn`
  route. One text turn with full skill loop. Stateless: caller
  supplies history. Returns assistant text + event log so the
  SetupAssistant UI can render skill calls in the transcript.

**Dashboard**:
- `components/setup/SetupAssistant.tsx` (new) — split-pane: chat
  with mic-toggle + text input on the left, live preview of the
  draft agent on the right (SWR-poll every 2 s). Voice via
  `/ws/voice`, text via the new `/turn` route — **both write to
  the same persistent draft state** via the Setup Assistant
  agent's `channels.setup_state.draft_agent_id` JSON column.
- `app/dashboard/agents/new/page.tsx` — refactored into a chooser:
  no `?mode` → Form/Voice cards; `?mode=voice` → SetupAssistant;
  `?mode=form` → the existing form (preserved verbatim under
  `FormFlow`). Wrapped in `<Suspense>` for `useSearchParams`.
- `app/page.tsx` — public landing CTA promoted: "🎙 Build by voice"
  is now the primary gradient button; "Open dashboard" demoted to
  outline.

### Key design choice

Voice + text hybrid only works if draft state survives transport
switches. We moved the `draft_agent_id` stash from per-runner
`ctx.metadata` (ephemeral) to the Setup Assistant agent's own
`channels.setup_state` JSON column (persisted). Both modes converge
on the same agent and the same persisted state — user can speak one
turn and type the next.

### Out-of-scope (explicit per the locked plan)

- API-key / token / webhook-URL dictation stays form-only.
- MCP server configuration via voice — same.
- Edit-by-voice on published agents — Session 11+ if anyone asks.

---

## Session 11 — 2026-05-18 (post-merge polish + telephony quality)

**Goal**: shake out real-user feedback against the new Telegram
pipeline + Setup Assistant + voice paths. Six bug-classes hit and
shipped in five commits.

### What was broken vs. what landed

| Symptom | Root cause | Fix |
|---|---|---|
| Telegram bot ignored all messages | Node gateway stubs returned 200 OK without proxying to core (`packages/server/src/routes/telephony.ts`) | Removed `telephonyRoutes` registration; `/api/v1/telephony/*` falls through to `proxyRoutes` ([bc2d53c](https://github.com/amznsri/openvox/commit/bc2d53c)) |
| Voice → "I couldn't make out what you said" always | Telegram delivers OGG-Opus as `.oga`; pydub ext list had `ogg` not `oga` → ffmpeg "Invalid data" | Normalise `oga→ogg` in `_telegram_transcribe` + add to recogniser ext list ([46da6a1](https://github.com/amznsri/openvox/commit/46da6a1)) |
| Text → voice reply reads "Function call begins, query_documents parameters..." | `_handle_telegram_update` called `llm.chat()` without `tools=` — LLM hallucinated function call as plain text | Replace with full skill loop (mirrors `agent_text_turn`) ([46da6a1](https://github.com/amznsri/openvox/commit/46da6a1)) |
| Voice reply says "asterisk asterisk" | LLM emits `**bold**` markdown; TTS reads chars literally | `clean_for_tts` strips markdown ([d63e429](https://github.com/amznsri/openvox/commit/d63e429)) |
| Voice reply says "dash" inside compound words | Hyphens in `real-human` etc. read as "dash" by BytePlus TTS | Hyphen-between-alpha → space; multi-dash → comma+space ([7bdee64](https://github.com/amznsri/openvox/commit/7bdee64)) |
| URLs spelled letter-by-letter, emoji read as "white heavy check mark", repeated `!!!` spluttery | Same broad family — raw LLM text to TTS | Comprehensive `clean_for_tts` sweep: URLs stripped, emoji removed, HTML entities decoded, repeated punctuation collapsed. Companion `looks_like_real_speech` rejects ASR noise before LLM ([8b83dab](https://github.com/amznsri/openvox/commit/8b83dab)) |
| Playground LLM Model field shows stale `doubao-seed-1.6-250615` | Hardcoded in `playground/page.tsx:27` (missed during the Python sweep) | Default to `""` with placeholder "(use provider default from .env)" ([bc31bf1](https://github.com/amznsri/openvox/commit/bc31bf1)) |
| Setup Assistant created agents with empty `llm_model` column | `InstantiateTemplateSkill` didn't pre-fill from settings the way the regular instantiate route does | Pre-fill `llm_model` + `voice_id` from `settings.byteplus_*` in `defaults` dict ([bc31bf1](https://github.com/amznsri/openvox/commit/bc31bf1)) |
| Random voice activation "every few seconds" with no mic interaction | Playground page had no cleanup `useEffect`; mic stayed open across navigation → STT transcribed ambient noise → LLM responded → TTS spoke | Aggressive teardown on `visibilitychange` + `pagehide` + unmount, in both Playground and SetupAssistant ([bc31bf1](https://github.com/amznsri/openvox/commit/bc31bf1)) |
| "Delete agent" silently failed for agents with prior Session rows | Delete route's cascade missed five tables added by Session 8/9 (EvalRun, Recording, ScheduledJob, JobRun, **the Sessions FK itself**). FK violation thrown, dashboard caught it silently | Route cascades through eight tables in dependency order; dashboard surfaces errors via `alert()` and invalidates SWR caches ([af6dd8b](https://github.com/amznsri/openvox/commit/af6dd8b)) |

### Lessons logged to CLAUDE.md §8

Bugs #47 (gateway transparent-proxy rule), #48 (third-party audio
extension normalisation), #49 (text-mode handlers must pass `tools=`),
#50 (clean_for_tts is mandatory on every TTS path), #51 (sweep
literals across the WHOLE monorepo, not one language), #52 (every
WS/mic/AudioContext consumer needs visibility+unload teardown),
#53 (FK-cascade family now at 3 occurrences — overdue for an Alembic
migration adding `ondelete="CASCADE"` to every agent-referencing
table).

### New TTS sanitiser is universal

`openvox/utils/text.py` `clean_for_tts()` handles:
markdown emphasis, hyphens-in-compound-words, multi-dash, URLs,
emoji ranges, HTML entities, repeated terminal punctuation, tab +
multi-space normalisation. Wired into:
- `pipeline/orchestrator.py:_speak()` — voice WS / playground.
- `api/routes/telephony.py:_telegram_synthesize_ogg()` — Telegram
  voice replies.

Future TTS-emitting paths (WeChat audio, Lark audio, Twilio outbound
voice when wired) inherit it automatically by going through these
two functions.

### Verification

Each fix verified live. The earlier 4-turn Setup Assistant E2E
remains green. Telegram voice + text round-trips work cleanly with
the Doc Assistant agent.

---

## Session 12 — 2026-05-21 (UX polish: Schedules + Templates for non-technical users)

**Goal**: smooth two friction points reported during a fresh-laptop
spin-up of the dashboard. Both are user-facing UX issues, no
provider/backend bugs.

### What was broken vs. what landed

| Symptom | Root cause | Fix |
|---|---|---|
| Schedules → "New schedule" exposed `cron / interval / once / webhook` directly — opaque to non-technical users (cron syntax, ISO datetime hand-edit) | UI passed raw trigger schema fields straight through | Added **Simple ↔ Advanced** mode toggle inside the JobModal. Simple = Date picker + Time picker + Repeat dropdown (Doesn't repeat / Hourly / Daily / Weekly / Monthly) with a live "Translates to:" cron preview. Selections translated client-side into the existing trigger schema so APScheduler engine + DB schema are untouched. Editing existing schedules defaults to Advanced to avoid lossy reverse-translation. ([dba8200](https://github.com/amznsri/openvox/commit/dba8200)) |
| "Weekly Saturday" Simple-mode trigger fired on Sunday | **APScheduler quirk**: `CronTrigger.from_crontab()` forwards the 5th field straight to `CronTrigger(day_of_week=…)` which uses Mon=0..Sun=6 — NOT Unix cron's Sun=0..Sat=6. JS `getDay()` Sun=0..Sat=6 sent dow=6 expecting Saturday; APScheduler interpreted 6 as Sunday | Remap JS dow → APS dow via `(jsDow + 6) % 7` in the Simple-mode translation table. Same commit. |
| Templates → "Use template" button: ambiguous (opens existing? copies?) + repeated clicks accumulated identical names (`Acme Support Voice`, `Acme Support Voice`, …) | Frontend `instantiate()` had an OK/Cancel confirm dialog that contradicted the button label; backend `instantiate_template` didn't dedupe names | Renamed button → **"Copy template"** (honest action label). Removed the confirm dialog. Backend `_next_available_agent_name()` appends ` (N)` suffix on collisions: `Acme Support Voice` → `(2)` → `(3)`. ([cf6734e](https://github.com/amznsri/openvox/commit/cf6734e)) |

### Lessons logged to CLAUDE.md §8

Bug #60 (APScheduler from_crontab DOW mismatch — must remap JS
getDay).

### Verification

Five POSTs to `/api/v1/templates/ecommerce-support/instantiate` — got
`Acme Support Voice`, `(2)`, `(3)`, `(4)`, then fresh `Science Tutor`
on first education-tutor copy. Weekly trigger with Saturday-picked
date now correctly resolves `next_run_at` to a Saturday.

---

## Session 13 — 2026-05-22 (voice-pipeline quality overhaul + user-driven barge-in)

**Goal**: address a torrent of user-reported voice-setup bugs in one
deep pass. The "build by voice" flow was leaking reasoning tokens,
hallucinating Chinese fillers, cutting users off mid-thought, and
ignoring "stop". Five distinct defects, three of them latent for
multiple sessions. Two commits shipped.

### What was broken vs. what landed

| Symptom | Root cause | Fix |
|---|---|---|
| Chinese fillers (`嗯。`, `啊。`) appeared as USER turns even in English sessions; assistant kept responding | BytePlus Seed-ASR streaming request omitted `audio.language` → model ran in auto-detect mode and defaulted to **Chinese** on silence / breath / lip smack. Confidence floor (<0.5) never fired because Seed reports either `confidence=0.0` (no value) or `>=0.5` for its own hallucinations | (1) `byteplus/stt.py` streaming `start` payload now sets `audio.language = config.language` (mirrors batch endpoint convention). (2) New `sanitize_user_final()` in `utils/text.py`: trims leading/trailing filler chars + CJK punctuation so `嗯。create` → `create`; drops pure-filler / ≤3-char pure-CJK on non-zh agents. (3) Logs every accepted user_final at INFO. ([a2f4823](https://github.com/amznsri/openvox/commit/a2f4823)) |
| Visible `</think_never_used_51bce0c785ca2f68081bfa7d91973934>` and step-number narration (`"per step 6"`) leaking into chat + TTS | Seed-2-Pro emits chain-of-thought inside `<think>…</think_HASH>` blocks; close tag carries a per-session **random hash** so naïve regex strips can't catch it across streaming chunks. No filter existed. | New streaming `ReasoningStripper` state machine in `utils/text.py`: holds back tag-boundary text until resolved, drops the entire `<think>` block. Wired into `orchestrator._llm_turn()` — display tokens, TTS buffer, and LLM history all see only clean text. `clean_for_tts` defensively re-strips as a last-line safety net. |
| Users cut off mid-sentence during natural pauses | BytePlus `end_window_size: 800ms` — too aggressive for thinking pauses | Bumped to **1500ms**. Matches Twilio/Vapi/LiveKit Agents' industry default range. |
| `recommend_template` matched `ecommerce-support` for "search web and **return** top 10 news" | Substring `if kw in desc` matcher: single keyword `return` hit despite being unrelated context | Rewrote as score-based with `\b`-anchored regex. ≥2 hits → 0.85 confidence; 1 hit → 0.4 + `recommend_custom=true`; 0 hits → 0 + explicit "build custom" hint. Surfaces up to 2 runners-up. |
| Even when user asked for "custom agent with web search", LLM instantiated `document-qa` template (skills: query_documents, analyze_image) | No `create_custom_agent` skill existed — `instantiate_template` was the only "create" tool the LLM had | New `CreateCustomAgentSkill` builds a blank Agent (template_id="") from name + skills list + optional system_prompt/greeting. Wired into setup-assistant's `skills` list and the system prompt's 3-CUSTOM workflow branch. |
| Setup Assistant didn't pick up template/prompt updates after deploys | Existing Setup Assistant Agent row carried the prompt **snapshot** from its first instantiation; template changes never propagated to running agents | `setup_assistant_singleton` now reconciles `system_prompt`, `greeting`, `skills` against current template defaults on every GET. Voice_id / temperature / max_tokens / llm_model are owner-tunable and stay untouched. |
| Assistant narrated workflow steps (`"now ask for greeting first, per step 6"`); read agent_ids and skill_result JSON out loud | Setup Assistant system prompt lacked output-hygiene rules | New HARD RULES: NEVER mention step numbers; NEVER read agent_ids / UUIDs / JSON; cap turns <20 words. Added curated `_SKILL_CATALOGUE_TEXT` so LLM can map user phrasing ("web search" → `web_search`) when picking skills. |
| User said "you can stop" — assistant kept talking | Server-side Silero VAD is loaded + wired but never fires `speech_start` during TTS playback. Hypothesis: browser AEC isn't perfect, TTS bleed-through keeps VAD in continuous `in_speech` state (no silence→speech transition to detect) | Two complementary **client-side** trigger paths, both feeding the existing `session.interrupt()` backend. (1) Visible **Stop button** appears in the composer while micState=speaking; sends `{"type":"interrupt","source":"button"}` + drains AudioPlaybackQueue locally. (2) **Browser-native stop-word listener** — second `webkitSpeechRecognition` instance runs in parallel with TTS, matches `stop|pause|wait|halt|cancel|quiet|hold on|be quiet` (+ 停/停下/暂停 for zh-*), fires the same interrupt path with `source="voice"`. (3) Backend logs interrupts with source tag for debuggability. ([193ff79](https://github.com/amznsri/openvox/commit/193ff79)) |

### Lessons logged to CLAUDE.md §8

Bug #61 (BytePlus Seed-ASR auto-detect defaults to Chinese — pin
language on every streaming start payload), #62 (reasoning-model
hash-suffixed close tags `</think_HASH>` need streaming-aware regex,
not `re.sub`), #63 (singleton endpoints must self-heal against
current template snapshots — DB rows are point-in-time copies),
#64 (server-side VAD interrupt unreliable when AEC isn't perfect —
client-side stop-word listener is the pragmatic complement).

### Architecture notes

The new helpers in `openvox/utils/text.py` (`ReasoningStripper`,
`strip_reasoning_tags`, `sanitize_user_final`) are central — any new
TTS-emitting path or STT-consuming path should reuse them rather
than reimplement. `clean_for_tts` now strips reasoning tags as the
last line of defence; future model families that emit
`<thinking>…</thinking>` or `<reasoning>` variants are covered by
the same regex.

### Verification

27/27 truth-table tests pass inside the running core (ReasoningStripper
across split chunks / orphan tags / unclosed-at-EOS; sanitize_user_final
on filler prefixes, pure-filler drops, CJK floor, language gating;
recommend_template scoring; create_custom_agent registration).

Live LLM round-trip: the user's exact "create a news agent" prompt
produces a 10-word reply, calls `create_custom_agent(skills=["web_search"])`,
shows NO reasoning tags / UUIDs / step-number narration.

Direct WS test of barge-in: `{"type":"interrupt","source":"button"}`
landed cleanly; `interrupt()` orchestrator method logs the speaking
state at the moment cancel was set.

---

## Session 14 — 2026-05-23 (Test-voice regression + Telegram tunnel rehome)

**Goal**: shake out two follow-ups from a fresh-laptop spin-up.
Small session, two commits + one config change.

### What was broken vs. what landed

| Symptom | Root cause | Fix |
|---|---|---|
| Agents → Voice & model → "Test voice" rendered the raw HTML of the dashboard's own 404 shell in the error toast | `testVoice()` in `apps/dashboard/src/app/dashboard/agents/[id]/page.tsx` used `fetch("/api/v1/playground/synthesize", …)` — a **relative URL**. Dashboard runs on :3000 (Next.js); API gateway is on :3001. Relative URL resolved against the page origin, so the request never left Next.js, which served its 404 HTML body. The `tts_provider` field it passed was also silently ignored — the backend's `SynthesizeRequest` model never had that field. | Replaced the hand-rolled `fetch` with the existing `api.synthesize()` helper from `lib/api.ts` (already imported, already builds `${BASE}/api/v1/...`, already parses `X-Sample-Rate`). Net diff: −22 / +20. Dropped the unused `tts_provider` clutter. ([6997af7](https://github.com/amznsri/openvox/commit/6997af7)) |
| Channels → Telegram showed "No public tunnel detected" — prior laptop had ngrok set up | Fresh laptop migration: `NGROK_AUTHTOKEN` in `.env` had been preserved from the old laptop's `.env` import, but the tunnel container wasn't running (it's `profile: tunnel` in docker-compose — opt-in) | `docker compose --profile tunnel up -d ngrok`. Tunnel registers at `https://<random>.ngrok-free.dev` → routes to `server:3001`. Confirmed via `/api/v1/telephony/public_url` returning `{"available": true, "source": "ngrok"}`. User then pasted BotFather token and bot went live. No code change. |

### Lessons logged to CLAUDE.md §8

Bug #65 (relative `/api/v1/...` URL in dashboard fetch lands at
Next.js 404, not the API gateway — all dashboard fetches must go
through `lib/api.ts` or include `${BASE}`).

### Verification

Backend curl: HTTP 200, 133 KB PCM, `X-Sample-Rate=24000`. Grepped
the whole `apps/dashboard/src` for sibling bugs — zero other
relative-`/api/v1` fetches. Telegram bot replied to a test message.

---

## Session 15 — 2026-05-22..23 (PLANNING_SESSION15 execution: Phase 2 + Phase 1 PR-1)

**Goal**: Execute the multi-phase roadmap in `PLANNING_SESSION15.md`. After a
research detour into OpenClaw / competitive landscape and a vendor-neutral
README rewrite, the substantive code work landed: Phase 2 (channel adapters
that don't need public URLs) and Phase 1 PR-1 (delete the Node gateway +
Redis). The "stack diet" promised by the audit shipped — 6 services → 4.

### What changed at a glance

| Component | Before Session 15 | After Session 15 |
|---|---|---|
| Stack size | 6 services (core/server/dashboard/postgres/redis/[ngrok]) | **4** (core/dashboard/postgres/[ngrok\|whatsapp]) |
| Telegram connect | Webhook only — required ngrok + public URL | **Polling default** — no ngrok needed |
| WhatsApp | Business API stub only (webhook, public URL) | **Personal QR-scan** added via whatsapp-web.js (opt-in profile) |
| WeChat | Work (official) supported | Same — Personal explicitly skipped (ban-risk decision documented) |
| Browser → core | Browser → :3001 (Node gateway) → :8000 (FastAPI) | **Browser → :8000 directly** |
| Auth endpoints | Hosted by Node gateway | Ported to FastAPI `api/routes/auth.py` |
| README positioning | "OpenClaw of voice agents", BytePlus-led | Vendor-neutral, multilingual-lead (commit `7c25034`) |

### Commits landed (chronological)

| SHA | Branch | What |
|---|---|---|
| `7c25034` | main | README vendor-neutral rewrite (researched OpenClaw — different product shape) |
| `bb24302` | main | Docs: capture Sessions 12-14 |
| `d1972bf` | main | `PLANNING_SESSION15.md` — full 4-phase plan committed |
| `a254a79` | phase1-spike | Phase 1 audit — refactor scope shrinks 2w→4d |
| `95ae56c` | phase1-spike | SQLite parity test (9/9 PASS) — Phase 1.1 needs no code |
| `805be51` | phase2-channel-adapters | Phase 2.1+2.2 — Telegram polling |
| `60579ca` | phase2-channel-adapters | Phase 2.5+2.6 — docs (WeChat skip + drop ngrok roadmap) |
| `261d7f0` | phase2-channel-adapters | Phase 2.3+2.4 — WhatsApp Personal QR adapter |
| `4b955e7` | phase2-channel-adapters | WhatsApp bridge: apt Chromium + Zscaler TLS toggle |
| `33217ca` | main | Merge phase2-channel-adapters |
| `8bad56a` | main | Merge phase1-spike |
| `(PR-1)`   | phase1-implementation | Delete gateway + Redis, port auth — `-454/+35 lines` |

### Strategic context captured along the way (Session 14 → 15 transition)

The Session 14 research round identified two corrections to my earlier
analysis:

1. **OpenClaw is NOT a voice-agent-builder**, it's a personal-assistant
   platform. Direct competitors are LiveKit Agents / Pipecat / Dograh / TEN.
   The "vendor-neutral, multilingual-lead" positioning landed in `7c25034`.
2. **OpenClaw's onboarding bar is matched by `curl install.sh | bash`** —
   not signed installers. Phase 4 scope reduced to paths A-D (pip/curl/brew/
   winget) with $0 ongoing cost; signed installers (path E) deferred.

The strategic decisions from that conversation are baked into
`PLANNING_SESSION15.md`:
- Same project, dual-mode (no fork) — industry pattern (Ollama, Streamlit, Jupyter).
- Drop ngrok built-in — solve the underlying channel-protocol choice instead.
- Skip WeChat Personal — account ban risk too high.

### Phase 1 spike findings (the big surprise)

`docs/phase1-audit.md` — committed on the `phase1-spike` branch, merged to
main as `8bad56a` — established the originally-planned 2-week Phase 1
refactor collapses to ~4 days:

1. **SQLite is already the default** (`config.py:49`). The "storage
   abstraction" sub-task needed ZERO code — SQLAlchemy's async engine
   handles both backends through the same path. The 9-case parity test
   at `packages/core/tests/test_storage_sqlite_parity.py` (also from
   the spike) is the regression coverage for that claim.
2. **Redis is declared but never used.** `redis_url` is in `config.py:50`
   and zero `*.py` files in `packages/core/openvox/` import it.
3. **Node gateway is 320 LoC of pure passthrough**, not a porting
   target. The dashboard can connect directly to core; only the auth
   scaffolds (~30 LoC) needed reimplementing.

### Phase 2 — Telegram polling (commit 805be51)

The biggest single non-tech UX win in this session. Before, connecting a
Telegram bot needed: ngrok account → auth-token in `.env` → `--profile
tunnel up`. After:

- `packages/core/openvox/telephony/telegram_polling.py` — per-agent
  background asyncio task long-polls Telegram's `getUpdates`. 30s
  polling interval, exponential back-off on errors, dispatches each
  update through the same `_handle_telegram_update` the webhook
  handler used. Lifecycle: `start_polling` / `stop_polling` /
  `start_all_pollers` (boot) / `stop_all_pollers` (shutdown).
- `TelegramConnectRequest` gets a `mode` field with `"polling"` as
  default. Existing webhook-mode agents (no `mode` field) are
  deliberately not auto-converted — they keep working via the
  unchanged webhook handler.
- Dashboard Channels → Telegram tab: new "Ingestion mode" picker;
  polling is preselected; the yellow "no public tunnel" banner is now
  gated to webhook mode only. Connect button no longer requires a
  public URL when polling is selected.

User verified end-to-end with their existing `ovoxdoc_bot` after
disconnect+reconnect in polling mode. Three edge cases also confirmed:
sending multiple messages quickly, stopping ngrok, restarting core.

### Phase 2 — WhatsApp Personal (commits 261d7f0 + 4b955e7)

A separate Docker service (opt-in via `--profile whatsapp`) running
Node + Express + whatsapp-web.js. Multi-agent multiplexed in one
process. LocalAuth persists session keys so reconnect after bridge
restart skips QR.

Two real bugs surfaced + fixed during the build:

1. **Chromium not found.** Original Dockerfile installed Chromium's
   runtime libs but relied on Puppeteer to auto-download a binary —
   which failed because `/root/.cache/puppeteer` path conventions
   changed between Puppeteer versions. **Fix**: install `chromium`
   from Debian apt, set `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium`
   + `PUPPETEER_SKIP_DOWNLOAD=true`, and explicitly pass
   `executablePath` to puppeteer.launch() in index.js (whatsapp-web.js's
   embedded puppeteer ignores the env var).
2. **Zscaler TLS interception.** Chromium failed
   `net::ERR_CERT_AUTHORITY_INVALID at https://web.whatsapp.com/` — same
   root cause `OPENVOX_INSECURE_TLS` solves on the Python side.
   **Fix**: bridge now reads the same env var; when true, adds
   `--ignore-certificate-errors` to Chromium launch args and logs
   the trade-off. docker-compose passes the var from `.env`.

Also surfaced (deferred): **stale Chromium profile lock** when the
bridge crashes mid-launch. The `SingletonLock` file in the volume
blocks the next start. Manual recovery is `docker volume rm
openvox_whatsapp-sessions`; auto-recovery on startup is a follow-up.

End-to-end smoke verified: bridge healthy, generates 6 KB PNG QR
within 3 seconds, Python core's `/whatsapp_personal/status` returns
it correctly. Real phone-scan test deferred (user doesn't have a
test number — primary WhatsApp is too risky given ban policy).

### Phase 1 PR-1 — Delete Node gateway + Redis (this commit on phase1-implementation)

Net change: **−454 / +35 lines**. Most invasive structural change in
the entire OpenVox history but actually low-risk because everything
the gateway did was either pure passthrough or trivially port-able:

| Deleted | New home |
|---|---|
| `packages/server/` (entire 320-LoC Node service) | n/a |
| docker-compose `server:` service | n/a |
| docker-compose `redis:` service + `redis-data` volume | n/a (dead config) |
| pnpm-workspace.yaml `packages/server` line | n/a |
| `/api/v1/auth/me` (gateway) | NEW `packages/core/openvox/api/routes/auth.py` |
| `/api/v1/auth/github/start` (gateway OAuth) | Same |
| `/api/v1/auth/google/start` (gateway OAuth) | Same |
| Dashboard `NEXT_PUBLIC_API_URL=http://localhost:3001` | now `http://localhost:8000` |
| Dashboard `NEXT_PUBLIC_WS_URL=ws://localhost:3001` | now `ws://localhost:8000` |
| ngrok `command: http server:3001` | now `http core:8000` |

Real bug encountered during the port: FastAPI rejected
`-> JSONResponse | RedirectResponse` union return-type annotations
("Invalid args for response field"). Fix was `response_model=None` on
the decorator.

Operator-upgrade note: anyone pulling this commit on an existing
install must one-time prune the orphan containers — docker compose
can't manage them once they're removed from the file:

```
docker stop openvox-server openvox-redis
docker rm   openvox-server openvox-redis
docker volume rm openvox_redis-data  # optional
docker compose up --build
```

This will be in PR-6's README update.

### Verification (Phase 1 PR-1)

- ✅ `docker compose up --build` → 4 services healthy.
- ✅ `curl :8000/health` → 200.
- ✅ `curl :8000/api/v1/auth/me` → `{"id":"local","name":"Local User"}`.
- ✅ `curl :8000/api/v1/auth/github/start` → 501 + readable error.
- ✅ `curl :8000/api/v1/agents` → existing 4 agents listed.
- ✅ `/ws/voice` endpoint exists on core (rejects malformed curl
  handshake; real browser upgrades succeed).
- ✅ Dashboard rebuilt with `NEXT_PUBLIC_API_URL=:8000` — loads at
  `localhost:3000`, hits core directly.

### Lessons logged to CLAUDE.md §8

Bugs #66 (Puppeteer cache-path failure in Docker — install apt
chromium instead), #67 (Zscaler TLS for Chromium — same
OPENVOX_INSECURE_TLS toggle as Python), #68 (stale Chromium profile
lock after subprocess crash — wipe the session volume to recover),
#69 (FastAPI union return-type annotation needs response_model=None),
#70 (orphan containers after `docker-compose.yml` rewrite — compose
won't clean up services it no longer knows about; document the manual
docker-stop/rm step in upgrade notes).

### Open at end of Session 15

- **Phase 2 phone-scan test** (T-WP-1, deferred — user has no test number).
- **Phase 1 PR-2 through PR-6** — CLI scaffold, static dashboard build,
  TESTPLAN re-run, README + docs/install-cli.md.
- **WhatsApp Personal stale-lock auto-recovery** — bridge could `rm -f`
  the SingletonLock file on startup if Chromium crashed previously.

---

## Session 16 — 2026-05-23 (Phase 4: native install + daemon mode)

**Goal**: Execute Phase 4 of `PLANNING_SESSION15.md` — install via any
of four free channels (pip / curl-bash / brew / winget) and run as a
background daemon. Shipped in a five-PR sequence on the
`phase4-native-install` branch.

### Commits landed (chronological, all on phase4-native-install)

| SHA | What |
|---|---|
| `31858d0` | PR-1: Daemon backends (launchd / systemd / nssm) + lifecycle commands (start/stop/status/restart/logs). 13 files, +1087 / −13. |
| `e85575f` | PR-2: PyPI packaging metadata — URLs, classifiers, postgres/whatsapp extras, sdist whitelist. Twine-passing wheel + sdist. |
| `783291b` | PR-3: `scripts/install.sh` — curl-bash installer; pipx-or-venv backend, Python ≥ 3.11 detect, PATH check. shellcheck-clean. |
| `e024841` | PR-4: Homebrew formula scaffold + WinGet manifest templates (installer / locale / version YAMLs). Both with documented release-pipeline substitution. |
| `9d41ffc` | PR-5: GitHub Actions release pipeline. Tag push → PyPI Trusted Publishing → GitHub Release with install.sh + checksum → Homebrew tap bump → WinGet PR. |
| `(this commit)` | PR-6: README quick-install table + `docs/install.md` consolidated guide + Session 16 entry. |

### What's now possible after Phase 4

```bash
# Any one of the four — pick by platform / taste
pip install openvox-core
curl -fsSL https://github.com/amznsri/openvox/releases/latest/download/install.sh | bash
brew install amznsri/openvox/openvox
winget install OpenVox.OpenVox

# Then
openvox start          # launchd on macOS / systemd --user on Linux / Windows Service on Windows
openvox status         # running as PID 12345
openvox logs -f        # ~/.openvox/logs/openvox.log
```

The release pipeline (PR-5) does PyPI + GitHub Release + Homebrew + WinGet
substitutions in a single `git push origin v0.x.y`.

### Daemon backend design notes

The `openvox/cli/daemon/` package picks a backend at runtime via
`platform.system()`. All three implement the same `DaemonBackend`
ABC so the five lifecycle commands stay platform-agnostic. Notable
per-OS gotchas captured in code comments:

1. **launchd**: `KeepAlive: {SuccessfulExit: false}` — restart on crash
   but NOT on clean `launchctl stop`. ThrottleInterval=10 prevents
   crash-restart spin loops.
2. **systemd --user**: services stop on logout unless
   `loginctl enable-linger` is set; documented in install-cli.md.
   Native `restart` verb override (cleaner than base-class stop+start).
3. **Windows / nssm**: Windows Service names can't contain `.`, so
   `SERVICE_NAME` is overridden to `OpenVoxDaemon`. nssm.exe bundling
   is wired in `pyproject.toml` but commented-out until the binary
   lands (release pipeline can fetch + verify checksum on first
   tag-publish).

### Tests

`packages/core/tests/test_daemon_backends.py` — 11 unit tests
covering: factory OS dispatch, plist & unit-text generation, launchd
+ systemd status parsing, Windows service-name invariants. All
subprocess calls mocked so the tests run on any OS.

### Lazy-import refactor (side benefit)

`openvox/cli/__init__.py` previously eager-imported `main.py` which
pulled in FastAPI / uvicorn / SQLAlchemy on every `import openvox.cli.*`.
Switched to PEP 562 `__getattr__` so `import openvox.cli.daemon`
(used by the unit tests + by external integrations) no longer pays
that cost. Console-script entry-point unchanged.

### Open at end of Session 16

- **First real PyPI publish** — needs the PyPI Trusted Publisher
  configuration done (one-time, manual, https://pypi.org/manage/
  account/publishing/), then bump version + `git tag`.
- **Tap repo creation** — `amznsri/homebrew-openvox` doesn't exist
  yet. Create empty repo + HOMEBREW_TAP_TOKEN PAT, then flip
  `ENABLE_HOMEBREW_PUBLISH=true` on the actions variable.
- **WinGet fork** — `amznsri/winget-pkgs` likewise, then
  `ENABLE_WINGET_PUBLISH=true`.
- **End-to-end macOS smoke** of `openvox start` → daemon registers in
  launchctl → dashboard reachable → `openvox stop` cleans up. Code-
  path verified via unit tests + plist generator output; needs a
  real `pip install -e .` + `openvox start` cycle on a Mac to close
  Phase 4's verification matrix.
- **`openvox onboard`** terminal wizard — only Phase 4 deliverable
  not in this branch. Dashboard wizard from Phase 3 covers the
  non-headless flow; terminal-only is a follow-up driven by demand.
- **nssm.exe bundling decision** — auto-download on first
  `openvox start` on Windows, or commit the binary + checksum to
  the repo + enable the force-include in pyproject.toml. Either
  works; needs a Windows test run to decide.

### Post-merge release validation (PR #2 + v0.1.0 — v0.1.5)

After Phase 4 merged via PR #1 (`f200aa2`), the four publish paths
were validated through a series of real tag pushes. Each surfaced a
distinct release-pipeline bug that the original PLANNING_SESSION15
plan didn't anticipate, all fixed via PR #2 (`release-pipeline-
fixes`, merged as `e73946d`) before cutting v0.1.5.

| Tag | What it tested | Result | Bugs found |
|---|---|---|---|
| `v0.1.0` | First real PyPI publish; Trusted Publishing | ✓ Worked first try | — |
| `v0.1.1` | Homebrew tap after HOMEBREW_TAP_TOKEN added | ✓ Worked first try; 143 resource blocks generated by poet | (Empty SHA256 in ~25 resources — wheel-only deps; spawned as task) |
| `v0.1.2` | WinGet after WINGET_PAT + ENABLE_WINGET_PUBLISH added | ✗ "wingetcreate.exe is not recognized" | Bug #71: not preinstalled on `windows-latest` |
| `v0.1.3` | Retry with `dotnet tool install Microsoft.WingetCreate.CLI` | ✗ "is not found in NuGet feeds" | Bug #72: wingetcreate isn't on NuGet either, despite some SO answers |
| `v0.1.4` | Retry with `aka.ms/wingetcreate/latest` download | ✗ "manifests/o/OpenVox/OpenVox was not found" (winget) + "Could not find a version that satisfies the requirement openvox-core==0.1.4" (homebrew) | Bugs #73 + #74 |
| `v0.1.5` | After PR #2 lands (PyPI-wait + submit-vs-update branching) | ✗ winget alone: "Resource not accessible by personal access token" | Bug #75: fine-grained PATs can't open cross-repo PRs |
| `v0.1.5` (rerun after PAT swap) | Classic PAT with `public_repo` scope | ✓ All five jobs green; **PR #378753 opened on microsoft/winget-pkgs** | — |

The final v0.1.5 run end-to-end:
- PyPI: https://pypi.org/project/openvox-core/0.1.5/ live.
- GitHub Release v0.1.5: wheel + sdist + install.sh + checksum.
- Homebrew tap (`amznsri/homebrew-openvox`): formula at v0.1.5
  with 143 resources, real version + sdist sha256 substituted.
- WinGet upstream PR: https://github.com/microsoft/winget-pkgs/pull/378753
  pending Microsoft validators + reviewer.

### v0.1.6 — `audioop` fix for Python 3.13+

The Phase 4 macOS smoke test on the user's machine surfaced a real
runtime bug: `pipx install openvox-core==0.1.5` into a Python 3.14
venv produces a daemon that crashes at startup with
`ModuleNotFoundError: No module named 'audioop'`.

PEP 594 removed `audioop` from the stdlib in Python 3.13.
`packages/core/openvox/api/ws/twilio_stream.py:41` imports it for
μ-law / PCM conversion on Twilio Media Streams.

Fix (`f8266e7`, shipped as v0.1.6): one-line conditional dep in
`packages/core/pyproject.toml`:

```
"audioop-lts>=0.2.1; python_version >= '3.13'",
```

`audioop-lts` is the canonical PyPI package that vendors the
removed stdlib module under the same name, so `import audioop`
works unchanged across all supported Python versions. Pip skips
the dep on 3.11/3.12 (stdlib has it) and installs it on 3.13+.

Smoke test on the user's Mac after upgrade:
- `pipx upgrade openvox-core` → 0.1.6
- `openvox start` → daemon stays alive across multiple status
  checks (5s apart, same PID) — was crash-respawning before
- `curl localhost:8000/health` → 200 returning `version: 0.1.6`
- `launchctl list | grep openvox` → real PID

**Phase 4 verification matrix is now CLOSED.** Daemon mode works
end-to-end on a real Mac with a real PyPI install.

### WinGet duplicate-PR edge case + cleanup

v0.1.6 also re-triggered the WinGet `submit` path because the
upstream merge of PR #378753 hadn't happened yet. Result: two
duplicate open PRs on microsoft/winget-pkgs (#378753 for 0.1.5
and #378758 for 0.1.6).

Manual cleanup at end of Session 16: closed PR #378753 (broken
0.1.5 — would have crashed for every Python 3.13+ user). PR
#378758 (0.1.6) left open for Microsoft to review.

Spawned follow-up to fix the workflow's submit-vs-update probe
to also check for OPEN PRs by us, not just upstream merge state.
Pseudocode in the spawned task; one-file `.github/workflows/
release.yml` change.

### PR #2 — release.yml fixes

PR #2 (`release-pipeline-fixes` branch, +76 / −9 in
`.github/workflows/release.yml`, no other files):

1. **Wait-for-PyPI-propagation step** in `publish-homebrew`. PyPI's
   upload endpoint and install index are eventually-consistent;
   propagation typically takes 30 s — 2 min. The job ran
   immediately after `publish-pypi` succeeded so it raced and
   randomly failed. Now polls `pip index versions openvox-core`
   until the new version appears (30 attempts × 10s = 5 min cap).

2. **Submission-mode branching** in `publish-winget`. `wingetcreate
   update` only works for packages already in microsoft/winget-pkgs;
   the FIRST submission of a new package needs `wingetcreate
   submit` against local manifest templates. The job now probes
   `https://api.github.com/repos/microsoft/winget-pkgs/contents/
   manifests/o/OpenVox/OpenVox` — 404 → submit-first-publish, 200
   → update. After PR #378753 merges, subsequent releases will
   auto-flip to `update`.

3. **wingetcreate install via aka.ms redirect**. Neither
   "preinstalled on windows-latest" (claim in v0.1.2 workflow
   comment) nor "dotnet tool install Microsoft.WingetCreate.CLI"
   (attempt in v0.1.3) actually works. Per
   https://github.com/microsoft/winget-create#installation the
   canonical install is `Invoke-WebRequest https://aka.ms/
   wingetcreate/latest`. Single-file standalone .exe, no NuGet,
   no dotnet dep.

### Bugs added to CLAUDE.md §8 (this session)

  #71 — wingetcreate.exe is NOT preinstalled on the `windows-latest`
        GitHub Actions runner image, despite some older Microsoft
        docs claiming so. Install via `aka.ms/wingetcreate/latest`
        download (canonical) or `dotnet tool install` (won't work,
        not on NuGet).
  #72 — `Microsoft.WingetCreate.CLI` is NOT a NuGet package; some
        Stack Overflow answers from 2022 suggesting `dotnet tool
        install` are misleading. Use the .exe download instead.
  #73 — `wingetcreate update` only works for packages already in
        microsoft/winget-pkgs. First submission of a new package
        needs `wingetcreate submit` against pre-rendered local
        manifests.
  #74 — `publish-homebrew` racing PyPI propagation: `pip install
        openvox-core==<new version>` immediately after `publish-pypi`
        fails ~80% of the time with "Could not find a version that
        satisfies the requirement". Poll `pip index versions` to
        wait for index propagation before installing.
  #75 — GitHub fine-grained PATs cannot open cross-repo pull
        requests to repositories outside the resource owner's
        account. For wingetcreate (which opens PRs from your fork
        of winget-pkgs to microsoft/winget-pkgs), this means the
        PAT MUST be a classic PAT with `public_repo` scope. The
        symptom is wingetcreate failing at the very last step with
        "Resource not accessible by personal access token" — the
        manifest validation succeeds, then the PR creation fails.

### v0.1.7 → v0.1.8 — the "Phase 4 isn't actually done" reality check

After v0.1.6 shipped and the daemon proved it stays running, I
declared Phase 4 complete. The user (correctly) pushed back: the
Phase 4 goal was "OpenVox without Docker for non-technical users",
and that means the dashboard has to work end-to-end, not just that
`openvox start` returns a PID. Their browser smoke test surfaced
five distinct bugs that the entire phase had glossed over:

| Bug | What broke | Root cause |
|---|---|---|
| #77 | `Test voice` button → 400 BytePlus TTS unavailable; `Build by voice` → system error | Phase 3 wizard saved keys to the encrypted store (`packages/core/openvox/secrets.py`) but providers still read `settings.<provider>_api_key` from pydantic-settings env vars. The two were never bridged. The hybrid resolver at `secrets.py:223` was written but never called by any provider. |
| #78 | Even after bridging, providers still saw empty keys | `register_builtins()` in lifespan instantiates each provider, and providers cache `settings.<key>` in `__init__`. Hydration was running AFTER registration → cached the empty value. Fix: reorder lifespan so hydrate runs between `init_db()` and `register_builtins()`. |
| #79 | `localhost:8000/dashboard/` → 404 | Next.js's static export with default config writes flat `dashboard.html` instead of `dashboard/index.html`. FastAPI's StaticFiles can't fall back to a sibling .html file. Fix: `trailingSlash: process.env.BUILD_OUTPUT === "export"` in `next.config.mjs`. |
| #80 | `localhost:8000/dashboard/` → landing page with unstyled HTML | The mount was pointing at `out/` directly (which serves the wrong file at the mount root) AND all Next.js asset URLs are `/_next/...` (root-relative). Fix: two separate mounts — `/_next` → `out/_next/` for assets, `/dashboard` → `out/dashboard/` for pages. |
| #81 | `localhost:8000/` → `{"service":"openvox-core","version":"0.1.0","docs":"/docs"}` JSON, blocking landing page | `health.py` registered `@router.get("/")` that returned hardcoded service info and shadowed any other root handler. Fix: delete the JSON root handler; add a `FileResponse` at `/` that serves the landing page (`out/index.html`). |

These all landed in v0.1.8 (`packages/core/openvox/api/app.py` +
`packages/core/openvox/api/routes/health.py` +
`apps/dashboard/next.config.mjs`).

### v0.1.8 — local-built + uploaded via twine (CI bypass)

GitHub Actions runs were inexplicably stuck in `queued` state with
zero runners allocated after a flurry of releases (v0.1.0–v0.1.6
plus retries). Even after making the repo public, the queue stayed
zombie — likely a per-account allocator hiccup that GitHub never
surfaced as an error.

Workaround for v0.1.7 + v0.1.8: build the wheel locally with
`hatch build`, then `TWINE_USERNAME=__token__ TWINE_PASSWORD=<token>
twine upload dist/*`. Same wheel as CI would produce; bypasses the
Actions queue entirely. PyPI Trusted Publishing (which we set up
for CI) is unaffected — the manual upload uses a per-project API
token instead.

### Honest meta-lesson (filed under "things I should know better")

I told the user "Phase 4 is complete" before they had clicked a
single button in the dashboard. The actual fixes for #77–81 are
small (~50 lines total), but the failure to TEST is the issue.
Going forward in this repo:

  - "Done" means **a human has run the thing end-to-end**, not
    "the daemon stays running".
  - Don't claim a packaged install works without a real
    `pipx install <project> && <click around>` cycle on a clean
    machine.
  - When the user is on the same machine, ask them to do the
    click-through; their browser is the only realistic test bed
    for the static-export-served dashboard.

This is captured in CLAUDE.md §8 as #82 (the meta-rule), with #77–81
documented as concrete bug entries.

### WinGet path closed pending PyInstaller redesign

Microsoft's winget-pkgs validator rejected PR #378787 (v0.1.8) with
`APPINSTALLER_CLI_ERROR_NESTEDINSTALLER_NOT_FOUND`: it extracted the
wheel and couldn't find `Scripts\openvox.exe` because that file is
generated by pip's console-scripts machinery at install time, not
bundled in the wheel. The whole "wheel as portable zip" approach was
structurally wrong.

Closed PR #378787 with a brief explanation. Three downstream changes:

  - `.github/workflows/release.yml`: publish-winget job now gated on
    a `PYINSTALLER_BUILT` repo variable that doesn't exist anywhere
    — effective kill-switch until a real Windows .exe ships.
  - `docs/install.md`: Path D section rewritten to say "WinGet not
    yet supported; use `pip install openvox-core` from PowerShell
    instead". Honest about what doesn't work.
  - `CLAUDE.md §8`: bug #83 added with the structural reasoning + the
    two real options (PyInstaller-package vs tell users to pip).

Spawned follow-up task: build a PyInstaller-packaged openvox.exe so
WinGet can ship a real installer. ~50-100MB; requires Windows runner
in CI; SmartScreen warnings without code-signing cert ($300+/yr) —
not blocking v0.1.x.

---

## Open follow-ups (carried forward)

Updated end of Session 14. Items shipped through Session 14 removed;
new follow-ups from Sessions 12-14 added at the end.

1. **Scheduler webhook trigger** (event-driven jobs).
2. **Skill hot-reload** (`watchfiles` on `~/.openvox/skills/`).
3. **Curated MCP server catalogue** with one-click pre-fill.
4. **CRM-via-MCP** for the SDR template (HubSpot / Salesforce snippets).
5. **Server-side VAD reliability** — Silero VAD provider is loaded
   and wired, but `speech_start` doesn't fire during TTS playback
   because browser AEC isn't clean enough. Session 13 worked around
   it with the client-side Stop button + browser stop-word listener.
   Real fixes: (a) server-side echo subtraction (subtract outgoing
   TTS PCM from incoming mic PCM by sample); or (b) continuous STT
   during TTS so stop-words detect server-side. Track this; the
   client-side fallback is good enough for now.
6. **Speech-to-Speech**: OpenAI Realtime adapter (BytePlus S2S not yet GA).
7. **Live interpretation**: simultaneous translation pipeline.
8. **Voice podcast generation**.
9. **BytePlus RTC client SDK** wiring (server-side token issuance done).
10. **Twilio Media Streams** ↔ pipeline bridge for the inbound path
    (outbound dial-out lands in Session 6; inbound Media Stream
    handler in WS is still scaffolded).
11. **WhatsApp Business inbound** message routing (verify done).
12. **Alembic migrations** (currently using `Base.metadata.create_all()`).
13. **Test suite** — `packages/core/tests/` is empty. Session 13
    landed a 27-case truth-table for `ReasoningStripper` +
    `sanitize_user_final` but those tests live in a `/tmp` script,
    not in a permanent pytest harness. **Promote them** into
    `packages/core/tests/test_text_helpers.py` and wire to CI.
14. **GCS, Alibaba OSS** storage implementations (interface defined).
15. **CLI**: `deploy`, `logs`, `dev` subcommands.
16. **Cloud-hosted multi-tenant mode** + OAuth (scaffold present, disabled).

### New from Sessions 12-14

17. ~~**Reserved ngrok domain**~~ — **SUPERSEDED in Session 15** by
    Telegram polling mode (commit `805be51` on phase2 branch).
    Telegram no longer needs a public URL when in polling mode (the
    new default). The ngrok sidecar stays available behind
    `--profile tunnel` for production WhatsApp Business / Twilio
    paths that still need inbound webhooks, but is no longer a
    non-tech setup requirement.
18. **Schedule trigger language hint per-agent** — `sanitize_user_final`
    branches on `agent_language.startswith("zh")` to know whether
    嗯/啊 are real or hallucinations. For zh-* agents the filter
    correctly passes them through. But the schedule's STT config
    inherits the agent's voice_language wholesale — confirm this
    plumbing covers all telephony paths (Telegram, Twilio, future
    WeChat/Lark).
19. **Auto-update existing agents from template** — Session 13 added
    self-heal for the **Setup Assistant** singleton only. Other
    templates' default fields don't propagate to existing agent rows
    after deploys. Decide policy: (a) leave as-is (template snapshots
    are intentional); (b) add an opt-in "resync from template" button
    on the agent edit page; (c) auto-resync only specific fields like
    `system_prompt`.
20. **Telegram channel — voice responses** — current implementation
    sends TTS-cleaned text back; doesn't auto-attach OGG-Opus
    synthesised audio. If users want voice notes, wire the existing
    `_telegram_synthesize_ogg` path into the standard reply flow,
    behind a per-agent toggle.
21. **Custom voice on `create_custom_agent`** — the new skill defaults
    voice_id to `settings.byteplus_tts_default_voice` (en_male_tim
    today). Should accept a voice_id parameter so the LLM can match
    user requests like "with a female voice".
