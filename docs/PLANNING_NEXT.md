# Planning — next session

Updated end of Session 8 (2026-05-14).

> **Detailed Session 8 plan** is preserved in
> [`docs/PLANNING_SESSION8.md`](PLANNING_SESSION8.md). This file rolls
> forward to Session 9.

---

## Shipped Session 8 — competitive-differentiation push

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

## Session 9 priority stack

Top of stack — these *complete* Session 8 features (backend live,
dashboard UI deferred) and unblock the post-fix-ups list. Pick from
the top.

### 1. Dashboard `/dashboard/evals` page — ~1 day
Backend is fully live (`/api/v1/evals/{recordings,personas,run,runs}`),
5 built-in personas seeded, judge verified end-to-end. Needed UI:
- List recent runs with pass/fail badge, click → judge breakdown
  drawer.
- "Run eval" wizard: pick agent → pick recording OR persona → enter
  criteria → run → poll. Same shape as the existing Skills tab.
- "Save as recording" button on any completed Session row in
  Observability.

### 2. Dashboard pricing breakdown card — ~0.5 day
Backend at `/api/v1/pricing/sessions/{id}` already computes per-
component cost + a sorted alternatives matrix. Needed UI:
- A "Cost" card on the Observability session detail (when we land
  that page) or inline on the per-session row.
- Stacked bar showing STT / LLM-in / LLM-out / TTS USD.
- "Switching X → Y saves $Z" recommendation card from
  `alternatives[0]`.

### 3. Image-size diet — ~0.5 day
Core image at ~9.7 GB because torch pulls CUDA wheels even though we
only need CPU inference. Already tried `download.pytorch.org/whl/cpu`
index in the Dockerfile (wrapped in `|| true`) but Zscaler blocks
that host. Workarounds:
- Test from a CI runner with unrestricted egress.
- Mirror the torch CPU wheels locally and serve via an in-network URL.
- Switch silero-vad to the ONNX backend (~80 MB) and drop torch
  entirely — saves the full ~3 GB.

### 4. Real provider-reported LLM token usage — ~0.5 day
Current `llm_tokens_in/out` columns are populated by a word-count
proxy in the WS forwarder. Plumb the real `usage.prompt_tokens` /
`usage.completion_tokens` fields from each LLM provider through
`LLMResponseChunk.raw` so the pricing calculator becomes accurate
to the cent.

### 5. Real WeChat Work / Lark audio bridge — ~1 day
Webhooks are mounted and signature-verified, but voice-message
decryption + download + transcription is marked TODO. Lands once we
have:
- A verified WeCom corp with EncodingAESKey we can test against.
- A Lark tenant with `tenant_access_token` flow exercised.

### 6. Scheduler webhook trigger — ~2 hrs (deferred from Session 7)
`POST /api/v1/jobs/webhook/{token}` for event-driven (vs cron) jobs.
Unblocks "external upload → process via agent" use cases.

### 7. Skill hot-reload — ~2 hrs (deferred from Session 7)
`watchfiles` on `~/.openvox/skills/`. On change re-run
`SkillRegistry._load_local_folder()` so new sessions pick up edits
without a restart.

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
