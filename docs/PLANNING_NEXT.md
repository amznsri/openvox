# Planning — next session

Updated end of Session 15 (2026-05-22..23).

> **State going into the next session: Phase 2 done on main; Phase 1 PR-1
> shipped on `phase1-implementation` branch (not yet merged); Phase 1 PR-2
> through PR-6 + Phase 3 + Phase 4 are the remaining work.** The
> committed plan continues to live in [`PLANNING_SESSION15.md`](./PLANNING_SESSION15.md);
> the Phase 1 spike findings in [`phase1-audit.md`](./phase1-audit.md) replaced
> the original 2-week Phase 1 estimate with ~4 days of actual work.
>
> The "stack diet" promised by the spike shipped in Phase 1 PR-1: 6 services
> → 4. Node gateway gone. Redis (which was never actually used) gone.
> Browser talks directly to FastAPI core. See `SESSION_LOG.md` Session 15
> entry for the full narrative.

Recent commits on `main`:
- `8bad56a` Merge phase1-spike — audit + SQLite parity (Session 15)
- `33217ca` Merge phase2-channel-adapters — Telegram polling + WhatsApp Personal (Session 15)
- `4b955e7` WhatsApp bridge: apt Chromium + Zscaler TLS toggle (Session 15)
- `261d7f0` Phase 2.3+2.4: WhatsApp Personal QR adapter (Session 15)
- `60579ca` Phase 2.5+2.6: Docs — supersede ngrok, document WeChat skip (Session 15)
- `805be51` Phase 2.1+2.2: Telegram polling (Session 15)
- `7c25034` README: vendor-neutral positioning (Session 14)
- `d1972bf` Docs: PLANNING_SESSION15.md (Session 14)

On `phase1-implementation` (not yet merged):
- `(PR-1)` Delete Node gateway + Redis + port auth scaffolds to FastAPI
  (−454 / +35 lines)

## Suggested next session

### Immediate (within Phase 1)

The 5 remaining PR-sized chunks of Phase 1 implementation, in priority order:

| PR | Effort | What |
|---|---|---|
| **PR-2** | 1 day | CLI scaffold (`openvox run` / `start` / `stop` / `status` / `restart` / `onboard` / `logs` / `version`). Replace stub `cli.py` with typer-based `cli/` package. Wire `webbrowser.open()` into `run` after `/health` returns 200. |
| **PR-3** | 0.5 day | Next.js static export — `output: 'export'` in `next.config.js`. FastAPI mounts `apps/dashboard/out/` at `/dashboard/*`. Enables single-process serving (dashboard + API on the same port) for CLI mode. |
| **PR-4** | (subsumed) | Auth endpoints already ported in PR-1. |
| **PR-5** | 0.5 day | Run full TESTPLAN P0+P1 in both modes (Docker + CLI). Fix any breakage. |
| **PR-6** | 0.5 day | `docs/install-cli.md` + README "Quick install" section + operator-upgrade notes (orphan container cleanup from PR-1). |

After Phase 1 lands on main: `pip install openvox && openvox run` is a
working dev-experience.

### Mid-term (Phase 3 + 4)

Both can begin right after Phase 1 PR-6:

- **Phase 3** (~1 week): First-run wizard for API keys, encrypted local
  store, template picker, optional channel connect. `openvox onboard` CLI
  equivalent. Specs in `PLANNING_SESSION15.md`.
- **Phase 4** (~2 weeks): Four free install paths (pip / curl-bash / brew /
  winget) + daemon mode (launchd / systemd / Windows Service). Specs in
  `PLANNING_SESSION15.md`. **One-time prerequisite — reserve `openvox` on
  PyPI** before Phase 4 starts. Free; takes 5 minutes; blocks the whole
  distribution story if the name is taken.

### Carry-forward from Session 15

| Item | Why deferred |
|---|---|
| WhatsApp Personal phone-scan E2E test (T-WP-1) | User doesn't have a separate test phone number; smoke test (QR generates) is as far as we can validate today |
| WhatsApp Personal stale-lock auto-recovery | Bridge could `rm -f SingletonLock` on startup if Chromium crashed previously; small ergonomic improvement |
| Telegram polling: per-sender history for WhatsApp Personal too | Currently each WA message starts a fresh LLM turn; should reuse sender_id as session key |
| Per-channel STT language-hint audit | Confirm `voice_language` reaches STTConfig on Twilio inbound + future WeChat/Lark voice paths |

### Original backlog (still pending)

Same items as the post-Session-14 list; nothing was pulled in or pushed
out in Session 15 beyond Phase 2:

1. **Scheduler webhook trigger** (event-driven jobs).
2. **Skill hot-reload** (`watchfiles` on `~/.openvox/skills/`).
3. **Curated MCP server catalogue** with one-click pre-fill.
4. **CRM-via-MCP** for the SDR template (HubSpot / Salesforce snippets).
5. **Server-side VAD reliability** (carry-forward from Session 13).
6. **Speech-to-Speech** (OpenAI Realtime adapter).
7. **Live interpretation** pipeline.
8. **Voice podcast generation**.
9. **BytePlus RTC client SDK** wiring.
10. **Twilio Media Streams** inbound bridge.
11. **WhatsApp Business inbound** routing (production webhook path).
12. **Alembic migrations** (replace `Base.metadata.create_all()` shim).
13. **Promote test_storage_sqlite_parity.py to CI** alongside the Session-13
    truth-table tests (still in `/tmp/...` rather than `packages/core/tests/`
    for those).
14. **GCS / Alibaba OSS** storage implementations.
15. **Cloud-hosted multi-tenant mode** + OAuth.

(Items 1, 13 are particularly low-risk and good first-tasks for a future
session that doesn't want to bite into Phase 3 yet.)

---

## Suggested next session — pick one of these tracks

### Track A — close out the three Session-9 deferrals
The lowest-risk, highest-shippable next session:

1. **Image-size diet** (~0.5 day) — Dockerfile already has
   `pip install --index-url https://download.pytorch.org/whl/cpu`
   wrapped in `|| true`. Re-run the build from any unrestricted
   egress (CI runner, home network, mobile hotspot). Expected to
   drop the core image from ~9.7 GB → ~6.7 GB by skipping CUDA
   wheels.
2. **WeChat Work / Lark audio bridges** (~1 day) — webhook receivers
   + signature verification already exist in
   `openvox/telephony/{wechat_work,lark}.py`. The missing piece is
   the audio download + transcribe + reply flow, modeled after
   `_handle_telegram_update`. Needs verified test credentials
   (WeCom EncodingAESKey + Lark tenant_access_token) before we
   can iterate.
3. **Telegram E2E pass** (~0.5 day) — pipeline shipped + text-side
   verified as of Session 11; Session 14 brought the bot live with
   ngrok. Worth one more pass with a real PDF-equipped Doc Assistant
   agent to confirm the full "Telegram → STT → query_documents →
   TTS → voice reply" loop stays clean given Session 13's voice
   sanitiser changes.

### Track B — `ondelete="CASCADE"` schema migration
The third FK-cascade bug landed in Session 11 (#53). The pattern is
predictable, the in-route cascades are bulletproof but verbose, and
every new table that references `agents.id` or `sessions.id` reopens
the same hole. Worth biting the Alembic-introduction bullet:

1. Add Alembic to the core package (~0.5 day).
2. Generate one migration that flips every FK in `db/models.py` to
   `ForeignKey("agents.id", ondelete="CASCADE")` and same for
   `sessions.id`. ~1 hour.
3. Audit `_ADDITIVE_COLUMNS` shim — once Alembic is wired, this
   pattern can retire too.

### Track C — Setup Assistant follow-ups
The Session-10 Setup Assistant + Session-13 voice-quality work
opened several surfaces real users will hit:

- **Edit-by-voice on published agents** — currently SetupAssistant
  only works on drafts. Adding an "Open this agent in voice mode"
  button on the agent detail page would let users tweak a deployed
  agent conversationally. Cheap to layer on.
- ~~Setup Assistant skill catalogue suggestions~~ **SHIPPED in
  Session 13**: `_SKILL_CATALOGUE_TEXT` baked into the system prompt,
  `create_custom_agent` skill creates blank agents from a skill
  list, score-based `recommend_template` returns `recommend_custom`
  when no template fits. What's left: **voice-driven skill ADD/REMOVE
  on existing agents** (today the LLM only sets `skills` at create
  time; can't say "add web_search to my Acme Support agent").
- **Custom voice picking** — `create_custom_agent` always uses the
  default voice. Accept a `voice_id` arg and have the LLM map user
  requests like "with a female English voice" → catalogue lookup.
- **Recordings → CI eval suite** — Session 8 shipped the recording
  + replay framework; nobody's actually wired a real eval suite
  against it yet. Tutorial / example workflow + a `make eval`
  target would unblock the value-prop.

### Track D — `clean_for_tts` provenance + Session-13 sanitiser hardening
Session 11 hit four TTS-quality bugs; Session 13 hit five more
voice-quality bugs (filler hallucinations, reasoning-tag leaks,
endpoint detection, template-snapshot drift, no barge-in). The
sanitiser surface is now substantial. Worth investing in:
- **Promote the 27-case truth-table to pytest** —
  `ReasoningStripper` + `sanitize_user_final` tests currently live
  in an ad-hoc `/tmp/voice_fix_tests.py`. Move to
  `packages/core/tests/test_text_helpers.py` and wire to CI.
- **Pre-flight LLM "voice-style" hint** in agent system prompts
  for new voice agents — defensive, catches the next class of
  TTS-hostile output before it ships.
- **Test harness that synthesises N sample LLM outputs** and
  surfaces any new mis-pronunciations / reasoning leaks / filler
  hallucinations before they hit users.
- **`dry-run` flag** on the orchestrator's `_speak()` for unit
  tests — no TTS API call, just collect the cleaned text the
  synth would have spoken.

### Track E — Voice-pipeline open issues from Session 13
Two things Session 13 deferred:

1. **Server-side barge-in** — Silero VAD is loaded + wired but
   `speech_start` doesn't fire during TTS playback. Browser AEC
   isn't perfect; TTS bleed-through keeps VAD in continuous
   `in_speech`. Two real fixes:
     (a) Server-side echo subtraction — buffer outgoing TTS PCM,
         subtract sample-aligned from incoming mic PCM. Most
         work, most robust.
     (b) Continuous STT during TTS — refactor the turn-based loop
         so STT stays open across TTS playback, then match the
         stop-word list server-side (instead of in browser).
         Smaller change but ties up an extra Seed-ASR stream.
   Session 13's client-side Stop button + browser-native listener
   are good enough for now; promote when needed for non-browser
   channels (Twilio, Telegram voice notes).
2. **Per-channel STT language hint propagation** — Session 13
   pinned `audio.language` on the streaming start payload from
   `STTConfig.language`, and the WS handler wires it from the
   agent's `voice_language`. Worth a second pass to confirm
   telephony paths (Twilio inbound, Telegram voice notes,
   future WeChat/Lark) all set this before instantiating STT.

Each of A/B/C/D/E is independently valuable. My recommendation:
**Track A** first (clears Session-9 deferrals, ~half a day),
then **Track B** (the FK-cascade migration debt — every new
agent-referencing table reopens that hole), then **Track D**
(test-harness the Session-13 sanitisers — they're load-bearing
for voice quality now). Track E only when a non-browser channel
actually needs barge-in.

---

## Shipped Session 9 — closing the Session-8 backlog

Five of seven priority items closed end-to-end.
Commits: [`a3b9a63`](https://github.com/amznsri/openvox/commit/a3b9a63)
(#6 + #7) → [`384e462`](https://github.com/amznsri/openvox/commit/384e462)
(#4 + #2 + #1).

- ✅ **#6 Scheduler webhook trigger** — `trigger_type="webhook"`,
  `POST /api/v1/jobs/webhook/{token}`, dashboard `WebhookUrlCallout`
  with copy-to-clipboard.
- ✅ **#7 Skill hot-reload** — `watchfiles` watcher over
  `~/.openvox/skills/` (or `OPENVOX_SKILLS_DIR`), wired into the
  FastAPI lifespan.
- ✅ **#4 Real provider-reported LLM token usage** —
  `LLMResponseChunk.usage` + `stream_options.include_usage=true`
  on BytePlus + every OpenAI-compat client. Orchestrator emits a
  `llm_usage` TurnEvent; WS forwarder + text playground use real
  counts when emitted, fall back to word-count when not.
- ✅ **#2 Pricing-breakdown card on Observability** — clickable
  rows → slide-in drawer with stacked-bar component cost +
  what-if matrix + "switch to X to save $Y" recommendation.
- ✅ **#1 Evals dashboard page** (`/dashboard/evals`) — full UI
  over the eval framework backend: stats row, recent-runs table,
  detail drawer with per-criterion judge breakdown + transcript,
  RunEvalModal for new runs, "Save as recording" button on the
  Observability drawer.

### Deferred — three items remain (all external-dependency-gated)

These are *code-complete or N/A*, not "I gave up". Pick up whenever
the gating dependency clears.

1. **#3 Image-size diet** — core container is ~9.7 GB because
   PyTorch pulls CUDA wheels. Dockerfile already has the CPU-only
   index install wrapped in `|| true`, but `download.pytorch.org`
   is Zscaler-blocked on this machine. Three escape hatches:
   - retry from a CI runner with unrestricted egress;
   - mirror the torch CPU wheels at an in-network URL;
   - swap silero-vad to the ONNX backend (~80 MB) and drop torch
     entirely (saves the whole ~3 GB).
2. **#5 Real WeChat Work / Lark audio bridges** — webhooks +
   signature verification work; voice-message decrypt / download /
   transcribe / reply remains TODO. Blocked on verified test
   credentials (WeCom EncodingAESKey + Lark tenant_access_token).
3. **Telegram end-to-end test** — pipeline shipped Session 9
   kickoff (commit `2e0fc7a`), but Docker daemon was down when
   the rest of Session 9 landed. Bring up `docker compose
   --profile tunnel up` once Docker is healthy, complete the
   `@BotFather` wizard, and verify voice in/out works.

---

## Shipped Session 10 — voice-driven Setup Assistant

Single commit [`71f47d2`](https://github.com/amznsri/openvox/commit/71f47d2),
~1300 LOC. Headline differentiation feature: non-technical users
create voice agents *by talking to a voice agent*. Both locked
decisions landed verbatim — voice + text hybrid input, first-class
CTA on landing + topbar.

- ✅ 6 new skills in `skills/builtin/setup.py`
  (`list_templates`, `recommend_template` [keyword classifier, no
  extra LLM round-trip], `instantiate_template`, `update_agent_field`
  [hard-coded field allow-list], `publish_agent`,
  `describe_remaining_setup`).
- ✅ `setup-assistant` built-in template with careful long-form
  system prompt + lower temperature (0.3).
- ✅ `GET /api/v1/templates/setup-assistant/singleton` — get-or-create
  so the dashboard doesn't accumulate SA agents per voice-setup
  click.
- ✅ `POST /api/v1/agents/{id}/turn` — stateless text turn with full
  skill loop. Mirrors orchestrator's `_llm_turn` for text mode.
- ✅ `components/setup/SetupAssistant.tsx` — split-pane voice + text
  chat on the left, live preview of the draft agent on the right.
- ✅ `/dashboard/agents/new` refactored to Form/Voice chooser
  (`?mode=form` and `?mode=voice` preserved as deep-link paths).
- ✅ Landing-page CTA: "🎙 Build by voice" promoted to gradient
  primary; "Open dashboard" demoted to outline.

**Key design choice making voice+text hybrid work**: draft state
lives on the Setup Assistant agent's own `channels.setup_state`
JSON column (not ephemeral `ctx.metadata`). Both transports converge
on the same persisted state — user can speak one turn and type the
next without losing context.

**Verified E2E** with four real BytePlus Ark turns:
*"I run a salon..." → recommended Receptionist → instantiated Acme
Salon → set greeting → described remaining setup → published.*

---

## Shipped Session 11 — post-merge polish + telephony quality

Five commits ([`bc2d53c`](https://github.com/amznsri/openvox/commit/bc2d53c)
→ [`af6dd8b`](https://github.com/amznsri/openvox/commit/af6dd8b))
shaking out real-user feedback. All bugs caught against the live
Telegram bot, voice agents, and Setup Assistant flow.

- ✅ **Gateway telephony stubs removed** — Node Fastify was
  intercepting `/api/v1/telephony/{telegram,whatsapp,twilio}/*` with
  stub handlers that returned 200 OK without forwarding to core.
  Bug had been silently losing every Telegram message. Removing the
  `telephonyRoutes` registration also unblocks WhatsApp + Twilio
  inbound paths when their creds land.
- ✅ **Telegram `.oga` decode fix** — voice notes arrive as OGG/Opus
  with `.oga` extension that pydub didn't recognise. Normalised
  `oga→ogg` in two places (per-call + ext list).
- ✅ **Telegram text-mode skill loop** — was calling plain
  `llm.chat()` without `tools=`; LLM hallucinated function calls as
  plain text. Replaced with full skill loop. Same fix shape as the
  `/api/v1/agents/{id}/turn` route.
- ✅ **`clean_for_tts` universal TTS sanitiser** (`utils/text.py`):
  strips markdown emphasis, hyphens-in-compound-words, multi-dash,
  URLs, emoji, HTML entities, repeated terminal punctuation, tabs +
  multi-space. Wired into orchestrator `_speak()` + Telegram TTS path.
- ✅ **`looks_like_real_speech` ASR noise guard** — rejects empty /
  single-char / pure-punctuation transcripts before they hit the LLM.
- ✅ **Stale `doubao-seed-1.6-250615` swept** from
  `playground/page.tsx:27` (we'd missed the TS side during the
  Python sweep). Field now defaults to `""` with placeholder.
- ✅ **InstantiateTemplateSkill pre-fills `llm_model` + `voice_id`**
  from settings — agents created by the Setup Assistant no longer
  land with empty columns.
- ✅ **Aggressive WS + mic teardown** on `visibilitychange` +
  `pagehide` + unmount in both Playground and SetupAssistant. Fixes
  the "voice activates every few seconds when idle" symptom: open
  mic + leaked WS was transcribing ambient noise → LLM responded →
  TTS spoke.
- ✅ **Agent delete bulletproof FK cascade** — now handles all 8
  tables that reference an agent (EvalRun, Recording, ScheduledJob,
  JobRun via ScheduledJob, Transcript via Session, Session,
  DocumentChunk, Document). Dashboard `destroy()` also gains
  try/catch with `alert()` + explicit `mutate("agents")` for
  immediate UI refresh.

Lessons logged to CLAUDE.md §8 bugs #47–#53.

---

## Older — Session 8 shipped (kept for context)

All nine items from PLANNING_SESSION8.md landed end-to-end:

**Defensive (3/3)**
- ✅ Silero VAD + sub-100 ms server-side interrupt
  (measured P50 = 58.5 ms, P95 = 121.7 ms).
- ✅ Twilio Media Streams inbound bridge (`/ws/twilio`, full
  protocol with μ-law⇄PCM resample + `clear` on barge-in).
- ✅ Browser SDK `@openvox/web` — React `<VoiceAgent />` + hook.

**Offensive — Bet A "Asia-Pacific positioning" (3/3, BytePlus stays
default but NOT exclusive)**
- ✅ Cross-provider pricing calculator with what-if matrix.
- ✅ WeChat Work + Lark inbound webhook handlers (voice bridge
  pending verified test credentials).
- ✅ 21 multi-language templates (3 use-cases × 7 languages,
  in-language `system_prompt`).

**Offensive — Bet B "Voice-agent eval framework" (4/4)**
- ✅ Recording + Persona library (3 new tables, 5 built-in personas).
- ✅ Replay runner + persona-vs-agent sparring runner.
- ✅ LLM-as-judge with per-criterion strict-JSON verdicts.
- ✅ GitHub Action example + `docs/EVALS.md`.

**Offensive — Bet C "MCP catalogue" (1/1, scoped down)**
- ✅ Curated catalogue (Slack/GitHub/Notion/HubSpot/Salesforce/Stripe)
  + `GET /api/v1/mcp/catalogue` + dashboard "Browse" modal.

Commits: `8d02382` (plan) → `a8c5d79` (checkpoint) → `1d4e770` (final).

---

---

## Older shipped (kept for context)

**Shipped Session 7 (bug-fix + UX polish pass):**
- ✅ TLS-bypassing skills swept (`get_quote`, `web_search` routed through
  `make_async_client`).
- ✅ `get_quote` migrated off Yahoo v7 (crumb-required) to v8/chart.
- ✅ `web_search` treats DDG 202 as empty rather than error.
- ✅ Orchestrator `skill_call` event now carries `data.args` so the
  dashboard transcript renders LLM arguments.
- ✅ `analyze_image` docstring documents Ark's server-side fetch + 403
  pitfall on bot-blocking hosts (Wikipedia etc).
- ✅ Publish button: busy state, optimistic SWR seed, success/error toast.
- ✅ Agent delete cascades through `documents` + `document_chunks`.
- ✅ Templates page: "N created" badge + confirm-on-duplicate flow.
- ✅ Top-bar search: real fuzzy popover (agents/templates/skills, kbd nav).
- ✅ Observability persistence: voice WS + text playground both write
  `Session` rows with `duration_ms`, `turn_count`, `first_token_ms`.
- ✅ Full 26/26 skill validation script (passes).

**Shipped Session 6:**
- ✅ Outbound lead qualifier (SDR) template + Twilio outbound dial-out + BANT skills.
- ✅ Multilingual customer-support IVR template + `detect_language` skill +
  `voice_map` per-language TTS routing.

---

## Deep backlog (further out — not Session 9)

- Speech-to-Speech via OpenAI Realtime adapter.
- Live interpretation / translation pipeline.
- Voice podcast generation (two-speaker).
- BytePlus RTC client SDK wiring on browser (token issuance done).
- Inbound WhatsApp / Telegram message routing (webhooks scaffolded).
- Alembic migrations (replace `_ADDITIVE_COLUMNS` shim once schema settles).
- Comprehensive test suite (`packages/core/tests/` still empty).
- GCS / Alibaba OSS storage implementations.
- CLI: `deploy`, `logs`, `dev` subcommands.
- Cloud-hosted multi-tenant mode + OAuth (scaffold present, disabled).
- Native CRM skills (HubSpot/Salesforce) — currently routed via MCP catalogue
  entries; native Python skill wrappers may be worth it once usage signals are clear.

---

## Sub-1-hour items still worth doing (carried from Session 7 backlog)

These didn't block any Session 8 work but remain quick wins:

- **`/skills/invoke` should accept an optional `agent_id`** so the
  Skills page can test `query_documents` end-to-end.
- **`transcribe_recording` local-file mode** — accept a `file_path`
  that routes through the streaming ASR path so agents can transcribe
  a container-local recording without a TOS round-trip.
- **Observability "first-token" is overstated** — it timestamps the
  first LLM token, not first-audio-out. Capture first-audio separately
  or rename the metric.
- **`Topbar` rendered per-page** — hoist into `app/dashboard/layout.tsx`
  so search state survives navigation.
- **Bulk-delete sessions** on the Observability page.
