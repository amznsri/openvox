# Planning — next session

Updated end of Session 9 (2026-05-18).

> **Detailed Session 10 plan** is locked in
> [`docs/PLANNING_SESSION10.md`](PLANNING_SESSION10.md). This file
> shows what shipped in Session 9 and the small punch-list of items
> still gated on external dependencies.

---

## Next session — Session 10 (Voice-driven Setup Assistant)

See [`PLANNING_SESSION10.md`](PLANNING_SESSION10.md) for the locked
spec. Two-and-a-half days of work — 5 new skills + 1 built-in
template + 1 chooser route + a landing-page CTA. Both user
decisions already locked: voice + text hybrid, first-class CTA on
the public landing page + dashboard topbar.

**Soft-gated**: Session 10 amplifies the voice pipeline to a much
broader audience, so the three deferred Session 9 items below are
worth clearing before kickoff if you can. Acceptable to start
Session 10 first if the deferrals stay external-dependency-bound
(WeChat/Lark credentials, etc.).

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
