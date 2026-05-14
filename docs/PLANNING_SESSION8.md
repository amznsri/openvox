# Planning — Session 8 (post-competitive-research scope)

Generated 2026-05-14 after market research surfaced **Dograh** as a direct
OSS competitor (self-hosted Vapi-alternative with dashboard + templates).
This plan picks the bets that give OpenVox structural differentiation
against the field — without overcommitting.

---

## 0. Decisions locked in this round

User selected **3 offensive bets** (modified) and **3 defensive items**:

### Bet A — multi-language / Asia-Pacific positioning *(scope adjusted)*
> **Key adjustment**: do NOT position OpenVox as a BytePlus-only platform.
> Keep the stack provider-agnostic; the differentiator is *Asia-Pacific
> messaging channels + cost transparency across all providers + multi-
> language templates*. BytePlus stays the default, not the exclusive.

- **A.1 — Cross-provider pricing calculator.** Real $/min per agent
  broken down by LLM / STT / TTS — comparing BytePlus vs OpenAI vs
  ElevenLabs vs Deepgram side-by-side. Pure dashboard feature on top
  of telemetry we already collect.
- **A.2 — WeChat Work + Lark as first-class channels.** Sit alongside
  WhatsApp + Telegram. WeChat Work has the most APAC-SMB market share
  in China; Lark is ByteDance's enterprise comms but globally
  applicable (we use it daily).
- **A.3 — Multi-language templates.** Service hotline, customer
  reactivation, and telesales in **English, Mandarin, Cantonese,
  Spanish, Bahasa Indonesia, French, Hindi**. 3 use cases × 7
  languages = 21 templates (or one template with `voice_map` filled
  out across the 7 — implementation choice covered below).

### Bet B — Voice-agent evaluation & regression testing *(full scope)*
- B.1 Conversation recording → replay against a new agent config.
- B.2 Synthetic-persona library (angry customer, confused elder,
  non-native speaker, in-a-hurry user, security-paranoid prospect).
- B.3 LLM-as-judge pass/fail evals against user-defined criteria.
- B.4 CI integration via `POST /api/v1/evals/run` and a sample
  GitHub Action.

### Bet C — MCP server catalogue only *(scoped down)*
- C.1 Curated catalogue of 6 servers (Slack, GitHub, HubSpot,
  Salesforce, Stripe, Notion).
- C.2 One-click install from dashboard with per-agent toggles.
- **Skipped from original Bet C**: voice-optimized MCP wrappers,
  "speaks MCP natively" pitch. Save for a future session if there
  are signals.

### Defensive — all three
- D.1 Silero VAD + measured interrupt latency <100 ms.
- D.2 Twilio Media Streams inbound bridge — phone calls reach the
  agent end-to-end.
- D.3 Browser SDK `@openvox/web` — React `<VoiceAgent />` component.

### Explicitly **out of scope** for Session 8
- Mobile SDKs (iOS / Android) — deferred until there's a real user ask.
- HIPAA / GDPR / PCI compliance certifications — marketing-only until
  someone actually needs it.
- Speech-to-speech via OpenAI Realtime — defer; sequential pipeline
  is competitive enough at <300 ms.
- Cost-optimization dashboard *recommendations* (Bet D from research)
  — the pricing calculator (A.1) is half of this; smart
  recommendations can come later.
- Agent versioning / branching (Bet E from research) — defer.

---

## 1. Sequencing — why this order

Dependency chain:

```
D.1 Silero VAD   ─┐
                  ├─►  D.2 Twilio inbound (needs accurate turn boundaries)
                  └─►  B.1 Replay (needs deterministic turn detection)

D.3 Browser SDK  ──►  (independent, can ship anytime)

C.1 MCP cat      ──►  (independent, small)

A.1 Pricing      ──►  (independent — uses existing Session telemetry)

A.2 WeChat/Lark  ──►  (depends on D.1 only for interrupt parity)

A.3 Templates    ──►  (independent — pure data)

B.2 Personas    ─┐
                 ├─► B.3 LLM judge  ──►  B.4 CI hook
B.1 Replay      ─┘
```

**Recommended timeline (15 working days):**

| Day | Item | Why this slot |
|---|---|---|
| **D1–D2** | D.1 Silero VAD | Foundational — blocks Twilio inbound and replay quality |
| **D3** | D.2 Twilio inbound | Now VAD is solid, real phone calls work |
| **D4** | D.3 Browser SDK | Independent + small, easy ship after telephony |
| **D5** | C.1 MCP catalogue | Tiny scope, high visibility |
| **D6** | A.1 Pricing calculator | Telemetry already exists, pure dashboard work |
| **D7–D8** | A.3 Multi-language templates | Pure data + voice mapping |
| **D9–D10** | A.2 WeChat Work + Lark channels | Two webhook adapters, similar pattern |
| **D11–D13** | B.1 + B.2 Conversation recording + persona library | Biggest schema additions |
| **D14** | B.3 LLM-as-judge | Adds skill + route |
| **D15** | B.4 CI hook + docs | Wrap up, README updates |

You can swap days if priorities shift, but **D1 (VAD) is non-negotiable
first** — every other item gets better with accurate turn boundaries.

---

## 2. Per-item detail

### D.1 — Silero VAD + interrupt latency

**Why**: Pipecat's headline feature. Without proper VAD, interruption is
crude (relies on STT partial latency, ~400–600 ms). Silero ONNX runs
locally at ~10 ms per 30 ms frame → sub-50 ms detection possible.

**Files to touch / create**
- `packages/core/openvox/providers/byteplus/stt.py` — wire VAD before STT
  consumes frames, or run in parallel and emit `user_speech_start` event.
- `packages/core/openvox/providers/vad/` *(new)* — `base.py`, `silero.py`.
  Add `VADProvider` interface to `providers/base.py`.
- `packages/core/openvox/pipeline/orchestrator.py` — consume
  `user_speech_start` events to fire `interrupt()` faster than the STT
  partial path.
- `packages/core/Dockerfile` — add `onnxruntime` + cache Silero model
  file at build time.
- `packages/core/pyproject.toml` — add `onnxruntime>=1.20` dependency.

**Data model**: no changes.

**API surface**: internal only. Add `VADConfig` to `providers/base.py`.

**Acceptance criteria**
- End-to-end voice interrupt: measure time from user's first audible
  syllable to TTS cancel event. Target **<100 ms** P50, **<150 ms** P95.
  Write the measurement as a Bash script in `scripts/measure_interrupt.py`.
- Silero falls back gracefully if ONNX runtime fails to load (log
  warning, keep using STT-partial-based interrupt — never crash).
- New `Agent.vad_provider` column (additive migration), default
  `"silero"`, `"none"` accepted to bypass.

**Risks**
- Onnxruntime image bloat — Silero model is ~2 MB but onnxruntime
  adds ~80 MB. Acceptable but document.
- Silero is CPU-bound; profile on the user's M-series Mac to confirm
  <10 ms per frame.

**Effort**: 2 days.

---

### D.2 — Twilio Media Streams inbound bridge

**Why**: We've shipped outbound Twilio dial-out (Session 6) but never
finished the inbound side. Real phone calls don't reach the agent today
— `/api/v1/telephony/twilio/voice` returns a TwiML stub.

**Files to touch / create**
- `packages/core/openvox/api/ws/twilio_stream.py` *(new)* — new WS
  endpoint at `/ws/twilio` that speaks Twilio's Media Streams protocol:
  - Receives JSON frames: `start`, `media` (base64 8 kHz μ-law),
    `mark`, `stop`.
  - Decodes μ-law → 16 kHz PCM, pipes into the existing
    `VoiceSession.push_audio()`.
  - Encodes TTS PCM frames → 8 kHz μ-law, sends as Twilio `media`
    frames with `payload` base64.
- `packages/core/openvox/telephony/twilio.py` — update
  `inbound_twiml()` to return `<Connect><Stream url="wss://…/ws/twilio?
  agent_id=…&call_sid=…"/></Connect>`.
- `packages/core/openvox/api/routes/telephony.py` — handle inbound
  webhook, look up agent by phone-number-mapping table.

**Data model**
- New `PhoneNumberMapping` table: `phone_number` PK, `agent_id` FK,
  `provider`. Or simpler: reuse a JSON column on Agent
  (`Agent.phone_numbers: list[str]`).

**Acceptance criteria**
- Call a Twilio number from a real phone, agent picks up with greeting,
  user-agent loop works for 3+ turns, hang up cleanly.
- Sessions page shows the call with `channel="phone"` and `caller_id`
  populated from Twilio's `From` field.
- Interruption works on phone (depends on D.1).

**Risks**
- Twilio expects 8 kHz μ-law and 20 ms frames; sample-rate conversion
  and re-framing is fiddly. `audioop` (stdlib) handles μ-law conversion;
  scipy or `pydub` for resampling.
- TTS playback latency on phone (audio path has more buffering than
  browser) — may need to tune the sentence-flush threshold.

**Effort**: 1 day if Twilio testing goes smoothly, 2 if not.

---

### D.3 — Browser SDK `@openvox/web`

**Why**: Today, embedding a voice agent in a customer's React app
requires reimplementing mic capture, PCM encoding, WS framing, and
audio playback. Vapi has a clean web SDK; we don't.

**Files to touch / create**
- `packages/sdk-web/` *(new package)* — TypeScript + Vite + rollup.
  - `src/VoiceAgent.tsx` — React component, props:
    `{ agentId: string, server: string, channel?: "rtc"|"ws" }`.
  - `src/useVoiceSession.ts` — hook that returns
    `{ status, transcript, start(), stop(), interrupt() }`.
  - `src/audio.ts` — mic capture (AudioWorklet), PCM s16le @ 16 kHz,
    playback queue with the look-ahead scheduling we already use in
    the dashboard.
  - `src/ws.ts` — WS client speaking the same protocol the dashboard
    uses today.
- `package.json` — `"name": "@openvox/web"`, declare React peerDep.
- `apps/dashboard/` — replace the inline playground audio logic with
  this SDK, prove the abstraction works.

**Data model**: none.

**API surface**: same WS protocol; no core changes needed.

**Acceptance criteria**
- `npm install @openvox/web` then 3 lines of code embeds a working
  voice agent on a fresh CRA / Vite / Next project.
- Dashboard playground refactored to use the same SDK (proves the
  abstraction).
- Bundle size < 30 KB gzipped (excluding React).

**Risks**
- Cross-browser audio quirks (Safari is fussy about AudioContext
  resume on user gesture — we've already solved this in the dashboard;
  port carefully).
- Publishing to npm registry is out of scope; just ship a runnable
  package the user can `pnpm install` from a local path.

**Effort**: 1 day.

---

### C.1 — MCP server catalogue

**Why**: We support MCP per-agent today but users have to type
`@modelcontextprotocol/server-github` from memory. A browseable list +
one-click "Use this" closes the loop.

**Files to touch / create**
- `packages/core/openvox/mcp/catalogue.json` *(new)* — 6 entries:

  ```json
  [
    {
      "id": "slack",
      "name": "Slack",
      "transport": "stdio",
      "command": "npx",
      "args": ["@modelcontextprotocol/server-slack"],
      "env_required": ["SLACK_BOT_TOKEN"],
      "tagline": "Read channels, send messages, search users.",
      "icon": "💬"
    },
    { "id": "github",     ... },
    { "id": "hubspot",    ... },
    { "id": "salesforce", ... },
    { "id": "stripe",     ... },
    { "id": "notion",     ... }
  ]
  ```
- `packages/core/openvox/api/routes/mcp.py` — add `GET
  /api/v1/mcp/catalogue` returning the JSON.
- `apps/dashboard/src/app/dashboard/agents/[id]/page.tsx` — MCP tab gets
  a "Browse catalogue" button → modal with cards. Click one → fields
  pre-fill in the existing MCP form. User pastes their env values and
  clicks Save.

**Data model**: none.

**API surface**: 1 GET route.

**Acceptance criteria**
- 6 catalogue entries visible on the MCP tab.
- "Use this server" pre-fills the existing form correctly.
- Per-agent toggle (already in current MCP tab) flips a server on/off
  without deleting it.

**Risks**
- HubSpot / Salesforce / Stripe MCP servers may not exist as
  `@modelcontextprotocol/server-*` — fall back to community packages
  (`@hubspot/mcp-server`, etc.) or remove from initial list.

**Effort**: 0.5 day.

---

### A.1 — Cross-provider pricing calculator

**Why**: The single biggest "is this for us?" question for a budget-
conscious team is "what will this cost". Nobody — Vapi, Retell, Bland,
Dograh — does cross-provider cost-per-session comparison well.

**Files to touch / create**
- `packages/core/openvox/pricing/` *(new)* — rate tables:
  - `rates.py`: `PROVIDER_RATES: dict[str, ProviderRates]` for the 14
    providers we ship. Per-minute or per-1k-tokens depending on the
    provider's billing model. Configurable via env var override.
- `packages/core/openvox/api/routes/pricing.py` *(new)*:
  - `GET /api/v1/pricing/rates` — current rate table.
  - `POST /api/v1/pricing/estimate` — body: `{agent_id, minutes,
    avg_input_tokens, avg_output_tokens}` → `{total_usd,
    per_component_usd, alternatives: [{config, total_usd}]}`.
- `packages/core/openvox/db/models.py` — add `Session.llm_tokens_in`,
  `Session.llm_tokens_out`, `Session.tts_chars` (additive migration).
- `packages/core/openvox/pipeline/orchestrator.py` — instrument tokens
  + tts chars per session.
- `apps/dashboard/src/app/dashboard/observability/page.tsx` — new
  "Cost breakdown" card per session: stacked bar STT / LLM / TTS /
  telephony, plus a "What if I switched to…" dropdown comparing total
  cost across provider combos.

**Data model**: 3 additive columns on Session.

**API surface**: 2 new pricing routes.

**Acceptance criteria**
- After a session ends, observability shows accurate cost ($0.0X)
  with breakdown.
- "Switch LLM" dropdown updates the estimate live without re-running
  the call.
- Rate table is configurable via `OPENVOX_RATES_FILE=/path/to/yaml`
  so users can override with their negotiated discounts.

**Risks**
- Provider pricing changes. Ship with last-known prices + a footer
  "rates as of YYYY-MM-DD, configure via OPENVOX_RATES_FILE".
- Twilio per-minute pricing varies by country — punt for v1, just
  use US rate, document the limitation.

**Effort**: 1 day.

---

### A.3 — Multi-language templates

**Why**: Today we have 1 multilingual template (`Polyglot Support`)
that auto-detects and uses `voice_map`. We need *language-specific*
templates with prompts written in-language so the agent feels native,
not translated.

**Decision**: one template per (use_case, language) pair, **not** one
template per use case with voice_map covering all languages. Reason:
the *system_prompt* itself has to be in-language for the LLM to
generate idiomatic responses. 3 use cases × 7 languages = **21 new
templates**.

**Files to touch / create**
- `packages/core/openvox/api/routes/templates.py` — add 21 entries.
  Use a helper:

  ```python
  def _hotline(lang: str, voice: str, prompt: str) -> dict:
      return {
          "id": f"hotline-{lang}",
          "category": "support",
          "language": lang,
          "default": {
              "system_prompt": prompt,
              "voice_id": voice,
              "voice_language": _BCP47[lang],
              "skills": ["lookup_order", "check_stock", "route_to_specialist"],
              ...
          },
      }
  ```
- `apps/dashboard/src/app/dashboard/templates/page.tsx` — filter chips
  by language ("All / EN / 中文 / ES / ID / FR / HI / ...") since the
  catalogue is now bigger.

**Data model**: none.

**API surface**: catalogue grows; existing endpoint serves it.

**Acceptance criteria**
- 21 templates visible on the Templates page.
- Language filter chips work.
- Each template instantiates with a sane voice for that language
  (BytePlus voice if activated, fall back to ElevenLabs multilingual
  v2, document which voices need user activation).

**Risks**
- Native-quality prompts in 7 languages is non-trivial — get the
  English versions polished, then have LLM translate + a native
  reviewer skim. Document that the non-English ones are "v0, please
  improve via PR".
- BytePlus may not have activated voices for Cantonese / Bahasa /
  Hindi on the user's key — note this in the template README so the
  user knows to either activate voices or swap to ElevenLabs.

**Effort**: 2 days (1 day prompts, 1 day voice mapping + dashboard
filter).

---

### A.2 — WeChat Work + Lark as first-class channels

**Why**: Adds APAC market relevance without leaning on BytePlus. Both
have callable voice APIs and group-message webhooks that fit naturally
alongside our WhatsApp / Telegram scaffolds.

**Files to touch / create**
- `packages/core/openvox/telephony/wechat_work.py` *(new)*:
  - Webhook receiver: `/api/v1/telephony/wechat_work/callback`.
  - Verify signature (msg_signature + timestamp + nonce + EncryptingAESKey).
  - On `voice` message: download voice file, transcribe via STT,
    push to VoiceSession, reply with TTS audio.
- `packages/core/openvox/telephony/lark.py` *(new)*:
  - Similar pattern; Lark uses event_v2 webhooks.
  - For voice: Lark IM supports audio messages; transcribe + reply.
  - Bonus: Lark "Bot" integration in group chats.
- `packages/core/openvox/api/routes/telephony.py` — wire both routes.
- `apps/dashboard/src/app/dashboard/agents/[id]/page.tsx` — Channels
  tab gains WeChat Work + Lark sections (webhook URL to copy +
  secret entry fields).

**Data model**
- `Agent.channels` is already a JSON column — add structured entries
  `{"wechat_work": {"corp_id": "...", "agent_id": "...", "token": "..."}}`
  and `{"lark": {"app_id": "...", "app_secret": "..."}}`.

**API surface**
- `POST /api/v1/telephony/wechat_work/callback`
- `POST /api/v1/telephony/lark/callback`

**Acceptance criteria**
- Send a voice message to the configured WeChat Work bot, get an
  agent reply in the chat.
- Same for Lark — message agent in a group, get a voice + text
  reply.
- Sessions page shows `channel="wechat_work"` or `channel="lark"`.

**Risks**
- WeChat Work requires a verified corp account to test end-to-end;
  user has one but signature verification is fiddly.
- Lark has both *internal app* and *external app* flows; start with
  internal (we have a tenant) and document external later.
- Voice message format: both platforms use `amr` or `mp3`; ensure
  `pydub` + `ffmpeg` handle them (probably yes).

**Effort**: 2 days. Lark first (we have a tenant), WeChat Work second.

---

### B.1 + B.2 — Conversation recording + persona library

**Why**: This is the wedge. Nobody else ships voice-agent regression
testing as a built-in feature.

**Files to touch / create**
- `packages/core/openvox/db/models.py`:
  - New `Recording` table: `id PK, session_id FK, audio_url,
    transcript_json, recorded_at, label`.
  - New `Persona` table: `id PK, name, system_prompt, voice_id,
    description, tags` (e.g. `["angry","customer","english"]`).
  - New `EvalRun` table: `id PK, agent_id, recording_id (nullable),
    persona_id (nullable), criteria_json, verdict, score, transcript,
    started_at, ended_at`.
- `packages/core/openvox/api/routes/recordings.py` *(new)*:
  - `POST /api/v1/sessions/{id}/save_recording` — promote a live
    session to a saved recording.
  - `GET /api/v1/recordings`, `GET /api/v1/recordings/{id}`.
- `packages/core/openvox/api/routes/personas.py` *(new)*:
  - CRUD.
  - Seed 5 built-in personas:
    - `angry_customer_en`: "You're a customer whose order arrived
      damaged. You're frustrated, want a refund, will escalate."
    - `confused_elder_en`: "You're 78 years old, don't understand
      tech well. Ask basic questions, get distracted."
    - `non_native_speaker_en`: "English is your second language.
      Sometimes use wrong grammar, occasionally code-switch."
    - `in_a_hurry_en`: "You have 30 seconds before a meeting. Keep
      it short, get the answer."
    - `security_paranoid_en`: "You're suspicious this might be a
      scam. Demand verification, refuse to share details."
- `packages/core/openvox/eval/` *(new)*:
  - `replay.py`: given a recording + a candidate agent config,
    replay user turns and capture candidate responses.
  - `persona_run.py`: given a persona + a candidate agent, run a
    conversation between persona-agent and candidate-agent until
    either resolves the criteria or hits a turn cap.
- `apps/dashboard/src/app/dashboard/evals/page.tsx` *(new)*:
  - List of recordings + personas.
  - "Run eval" wizard: pick recording or persona, pick target agent,
    optional criteria, click Run.

**Data model**: 3 new tables (additive, no FK to existing besides
agent_id and session_id).

**API surface**: ~6 new routes for CRUD + run.

**Acceptance criteria**
- Save a recording from a live session, replay against an agent
  variant, see diff in transcripts.
- Spin up a persona conversation: angry_customer_en vs Acme Support
  Voice — runs to 5–10 turns, transcript captured, ends when persona
  is satisfied or turn cap reached.
- Eval list page shows last 20 runs with pass/fail (B.3 fills the
  judge in).

**Risks**
- Persona × candidate is two LLM sessions talking to each other —
  cost can balloon. Cap turn count (default 10) and document.
- Replay fidelity: TTS voice cloning won't match the original speaker
  exactly. That's fine — we're testing the *agent's* responses, not
  reproducing the human side.

**Effort**: 3 days.

---

### B.3 — LLM-as-judge pass/fail

**Files to touch / create**
- `packages/core/openvox/skills/builtin/evals.py` *(new)*:
  - `EvaluateConversation` skill: takes `transcript` + `criteria`,
    returns `{verdict: pass/fail/partial, reasoning, scores}`.
  - Uses the agent's own LLM (or a dedicated `judge` LLM provider
    configurable per eval).
- `packages/core/openvox/eval/replay.py` + `persona_run.py` — after
  the run completes, invoke the judge skill, persist verdict to
  `EvalRun.verdict`.

**Acceptance criteria**
- 3 default criteria templates:
  - "Did the agent collect the order number?"
  - "Did the agent stay polite throughout?"
  - "Did the agent escalate when the user asked for a human?"
- Custom criteria: free-text field, judge interprets.
- Verdict appears on the eval list page.

**Effort**: 1 day.

---

### B.4 — CI hook

**Files to touch / create**
- `packages/core/openvox/api/routes/evals.py` — `POST /api/v1/evals/run`
  body: `{agent_id, recording_ids?, persona_ids?, criteria}` returns
  job id; `GET /api/v1/evals/runs/{id}` polls.
- `.github/workflows/eval.example.yml` — sample workflow that runs on
  PR open, hits the eval endpoint, fails the PR if any verdict is
  `fail`.
- `docs/EVALS.md` — how to use it.

**Effort**: 0.5 day.

---

## 3. Cross-cutting concerns

### Schema migrations
All new columns/tables go in `db/models.py` and the `_ADDITIVE_COLUMNS`
shim in `db/session.py:init_db()`. New tables `Recording`, `Persona`,
`EvalRun`, `PhoneNumberMapping` (if separate) come in via
`Base.metadata.create_all()` on next startup.

### Telemetry expansion
A.1 (pricing) needs `Session.llm_tokens_in/out` and `Session.tts_chars`.
B.1 needs `Recording.transcript_json`. Both should be best-effort —
never crash a session because telemetry can't write.

### Documentation
- Update `CLAUDE.md` §7 with new shipped features.
- Update `docs/SESSION_LOG.md` with a "Session 8" chapter as work lands.
- Update `docs/diagrams.md` §4 (extensibility surface) to add the eval
  framework as a fifth box alongside Skills / Templates / Scheduler / MCP.
- New `docs/EVALS.md` for the test-pyramid pitch.

### Versioning
This is a meaningful enough leap that we should tag a release after
Session 8: `v0.2.0` ("the polish + evals release"). Currently nothing
is tagged.

---

## 4. Done definition for Session 8

Session 8 is considered "done" when:

1. **All 9 items shipped** (D.1–3, C.1, A.1–3, B.1–4).
2. **Bugs #40+ added to CLAUDE.md** for anything painful discovered.
3. **Session 8 chapter in SESSION_LOG.md** lands the same day the
   last item ships.
4. **`docs/diagrams.md` updated** to reflect new modules.
5. **Tagged `v0.2.0`** with release notes pulled from SESSION_LOG.
6. **Demo recording updated** — slides.html still works, but a fresh
   ~90-second screencast covering: pricing calculator, multi-language
   templates, MCP catalogue, eval framework.

---

## 5. How to consume this plan in a future session

If you come back to this in a fresh Claude session:

1. **Read this file first.** It captures the *decisions* and the *why*,
   which CLAUDE.md doesn't.
2. **Check git log since 2026-05-14** to see which sections have
   already shipped.
3. **Check `db/models.py`** for the new tables — if `Persona` or
   `Recording` exist, Section 2.B has started.
4. **Don't re-litigate scope.** User explicitly rejected BytePlus-only
   positioning in §0 (Bet A); don't accidentally revert.
5. **D.1 (Silero VAD) is the gate.** If it hasn't shipped, do it first
   before anything in §B (replay quality depends on accurate VAD).

---

*Plan locked: 2026-05-14. Execute against this — don't re-plan unless
the user explicitly says so.*
