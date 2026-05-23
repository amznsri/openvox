# OpenVox — Manual Test Plan

> Run this after **any** non-trivial change. Each test has explicit steps and an
> expected outcome — pass/fail is unambiguous. Sections are ordered by blast
> radius: a regression in §1 breaks everything; a regression in §10 is cosmetic.
>
> **Status keys:** ✅ verified during a prior session · ⚠ verified once, no
> regression coverage · 🆕 added to this list but never run end-to-end ·
> 🚧 blocked on external dependency (test credentials, paid API, etc.)
>
> **Priority keys:** **P0** = smoke (run before every commit) · **P1** = run
> after touching the affected subsystem · **P2** = run weekly or before a tag.
>
> When a test fails, **file the failure as a bug in CLAUDE.md §8** with a fix
> note — that's how we keep regressions from recurring. The numbered bug refs
> below point back to the entry that originated the test.

---

## How to run

```bash
# Bring up the stack
cd /Users/bytedance/Documents/ByteDance/NewModelDemos/openvox-v2
docker compose up -d --build
docker compose ps                          # all six services Up
open http://localhost:3000

# After core / Python edits
docker cp packages/core/openvox/. openvox-core:/app/openvox && docker compose restart core
# After dashboard / TypeScript edits
docker compose build dashboard && docker compose up -d --no-deps dashboard
# Hard refresh the browser (⌘⇧R) between TS rebuilds.
```

A **10-minute smoke pass** is §1 + §2.1 + §3.1 + §6.1 + §11.1 (all P0).
A **full regression pass** runs every P0+P1 — about an hour.

---

## 1. Boot / infrastructure  (P0)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-101 | All services up | `docker compose ps` | core, dashboard, ngrok, postgres, redis, server all `running` | ✅ |
| T-102 | No restart loop | `docker compose logs --tail=20 core` | no `Traceback` or `Exception` | ✅ |
| T-103 | Schema migrations applied | `docker compose exec -T postgres psql -U openvox -d openvox -c "\d sessions"` | columns include `stt_chars`, `tts_chars`, `llm_tokens_in/out` (bug #28, #55, #57) | ✅ |
| T-104 | TLS escape hatch honored | `.env` has `OPENVOX_INSECURE_TLS=true` OR `docker/extra-ca.pem` populated | core logs show no `CERTIFICATE_VERIFY_FAILED` on first BytePlus call (bug #11) | ✅ |
| T-105 | Dashboard fresh bundle | After TS edit + rebuild + hard-refresh | new feature visible (bug #40 — stale Next.js build) | ✅ |
| T-106 | Hot-copy lands in container | `docker cp packages/core/openvox/. openvox-core:/app/openvox` then restart | listing inside container shows updated mtime — NOT nested as `/app/openvox/openvox/` (bug #41) | ✅ |

## 2. Voice pipeline  (P0 = 2.1, P1 = rest)

### 2.1 Smoke — end-to-end voice turn

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-201 | Playground voice turn | `/dashboard/playground` → Voice tab → pick "Acme Support Voice" → press mic → say "Where is order ORD-1001" → release | within ~1 second hears agent reply naming DHL/2 days; transcript shows user_final + assistant_done | ✅ |
| T-202 | First-audio latency | Same as T-201; measure ⏱ from mic-release to first audible word | < 1.5 s (target sub-300 ms server-side; client adds 60ms playback look-ahead) | ⚠ |
| T-203 | Interruption | During agent's reply, start speaking | TTS stops within ~100 ms; new utterance is captured; observability shows `interrupt` event | ✅ (P50=58.5 ms measured) |

### 2.2 STT correctness  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-211 | BytePlus STT final framing | Speak a 5-second sentence | `user_final` event arrives with correct text (bug #13 — 4-byte sequence parse) | ✅ |
| T-212 | BytePlus STT clean close | End session | no error logged; `ConnectionClosedOK` swallowed gracefully (bug #15) | ✅ |
| T-213 | Background-noise rejection | Stay silent for 10s while mic open | no spurious "I couldn't make out" messages; `looks_like_real_speech()` rejects them silently | ✅ |
| T-214 | Streaming STT language hint | core logs after mic-start | `stt language hint: en-US` (or zh-CN, etc.) emitted on every session — confirms `audio.language` reaches BytePlus and stops auto-detect-default-to-Chinese hallucinations (bug #61) | ✅ |
| T-215 | Filler-prefix trim | Speak "嗯。create a new agent" (or trigger a 嗯 hallucination then real word) | `user_final` text is `"create a new agent"` (filler stripped); log line shows `trimmed ASR filler affix:` (Session 13) | ✅ |
| T-216 | Pure-filler drop | Idle mic for 30s on en-US agent | no `user_final` events for 嗯/啊/哦 hallucinations; logs show `dropping ASR hallucination` with reason; LLM does NOT respond (Session 13) | ✅ |
| T-217 | Chinese agent unaffected | Set `voice_language=zh-CN` on an agent; speak `嗯` | filler is preserved (real turn); `sanitize_user_final` returns text unchanged for zh-* agents (Session 13) | 🆕 |
| T-218 | Endpoint detection 1500ms | Speak with a 1-second mid-sentence pause | STT does NOT promote to final during the pause; full utterance arrives as one user_final (Session 13, `end_window_size=1500`) | ⚠ |

### 2.3 TTS correctness  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-221 | Markdown stripped | Edit an agent's system prompt to make it reply with `**bold** text`; trigger voice turn | spoken output is "bold text", not "asterisk asterisk bold" (bug #50, `clean_for_tts`) | ✅ |
| T-222 | Hyphens normalised | Reply contains "real-human"; voice turn | spoken as "real human" not "real dash human" (bug #50) | ✅ |
| T-223 | URLs spelled sensibly | Reply contains `https://docs.example.com`; voice turn | NOT spoken as "h-t-t-p-s-colon-slash-slash"; either elided or read as "docs example com" | ⚠ |
| T-224 | Emoji stripped | Reply contains 🎉; voice turn | not spoken as "white heavy check mark"; silently dropped | ✅ |
| T-225 | HTML entities | Reply contains `&amp;`; voice turn | spoken as "and", not "ampersand a m p semicolon" (bug #50) | ✅ |
| T-226 | Voice ID valid | Pick any English template, voice turn | NO `code=55000000 message='resource ID is mismatched'` (bugs #25/#58/#59) | ✅ |
| T-227 | Voice catalogue dropdown | `/dashboard/agents/<id>` → Voice tab | dropdown lists 41 BytePlus voices; "Test voice" button plays a sample (bug #59) | ⚠ |
| T-228 | Reasoning-tag stripped from display | Use a Seed-2-Pro agent; voice or text turn that triggers reasoning | chat bubble does NOT show `<think>…</think_HASH>`; `ReasoningStripper` filters across streaming chunks (bug #62) | ✅ |
| T-229 | Reasoning-tag stripped from TTS | Same as T-228 | TTS does NOT speak "less than slash think…"; `clean_for_tts` defensively re-strips (Session 13) | ✅ |
| T-230 | Reasoning-tag stripped from history | After T-228, send a follow-up turn | LLM doesn't repeat its prior reasoning; history shows ONLY user-visible assistant text (Session 13) | 🆕 |

### 2.4 LLM round-trip + tool-calling  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-231 | Tool fragment accumulation | E-commerce agent: "Check order ORD-1001" | LLM emits one `tool_call` with merged JSON args; `lookup_order` returns; agent replies with carrier/ETA (bug #17) | ✅ |
| T-232 | Tool-call message ordering | Same as T-231; inspect history saved to DB | assistant message with `tool_calls` precedes tool reply; tool_call_id matches (bug #18) | ✅ |
| T-233 | Provider-reported usage | Voice turn; observability drawer | `llm_usage` populated with `prompt_tokens`/`completion_tokens` (real, not approx) (bug #45) | ✅ |
| T-234 | Tool-loop bound | Configure a skill that always calls another tool; trigger | session terminates after 6 iterations with `error` event, not stack overflow (bug #46) | 🆕 |

### 2.5 VAD + interrupt  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-241 | Silero loads | core logs show `vad: silero registered` at boot | success; no `No module named 'onnxruntime'` (bug #42) | ✅ |
| T-242 | `vad_provider="none"` falls back | Set agent's `vad_provider=none`; voice turn | works; interrupt latency higher (client-driven) | ⚠ |
| T-243 | Stop button cancels TTS | During SetupAssistant agent's long reply, click the red ⏹ button in the composer | TTS audio stops within ~20-40 ms; `[stopped — button]` line appears in chat; core logs `interrupt requested via WS (source=button)` + `interrupt() called — speaking=True` (bug #64) | ✅ |
| T-244 | Voice "stop" word cancels TTS | During reply, say "stop" or "pause" or "cancel" | TTS stops; `[stopped — voice]` line in chat; core logs `source=voice` (Session 13) | ⚠ |
| T-245 | Stop button disabled outside speaking state | Mic state = idle/listening | composer shows mic button NOT Stop button — no accidental interrupt while user is talking | ✅ |
| T-246 | Server VAD known-broken under TTS bleed-through | Trigger interrupt via voice during TTS playback | server-side Silero VAD does NOT fire `speech_start` reliably; client-side fallback (T-243/244) handles it. Track for future server-side AEC work (Session 13 deferred) | 🚧 |

## 3. Channels

### 3.1 Web RTC (browser WS)  (P0)

Covered by §2.1.

### 3.2 Telegram  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-321 | Webhook reaches core | Send text "List APIs" to bot connected to Doc Assistant | bot replies (text + voice); core logs show `_handle_telegram_update` ran (bug #47 — gateway no longer swallows) | ✅ |
| T-322 | Voice note → reply | Send a 5-sec voice note | bot transcribes (`.oga`→`.ogg` normalisation, bug #48); replies in voice + text | ✅ |
| T-323 | Skill loop runs | Doc Assistant: "summarise the API docs" | reply contains real `query_documents` result, NOT text like "Function call begins…" (bug #49) | ✅ |
| T-324 | Session row persisted | After T-321; check `/dashboard/observability` | new row with `channel=telegram` and both user + assistant Transcripts (bug #55) | ✅ |
| T-325 | Tunnel detection | With `docker compose --profile tunnel up -d ngrok` running, `curl /api/v1/telephony/public_url` | `{"available": true, "source": "ngrok", "url": "https://...ngrok-free.dev"}` — dashboard's Connect Telegram modal no longer shows "No public tunnel" banner (Session 14) | ✅ |
| T-326 | Tunnel-down graceful banner | Stop ngrok: `docker compose --profile tunnel down ngrok`; reload Channels page | yellow "No public tunnel detected" callout appears with the .env / ngrok instructions (Session 14) | ✅ |

### 3.3 Twilio  (P2)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-331 | Outbound dial | `POST /api/v1/telephony/twilio/place_call` with `{to, agent_id, callback_url}` | returns Twilio call SID; phone rings | 🚧 (real number required) |
| T-332 | Inbound Media Streams | Configure phone number webhook to `/ws/twilio`; call number | μ-law→PCM bridge works; agent speaks back; `interrupt` sends `clear` to Twilio | 🚧 |

### 3.4 WhatsApp / WeChat / Lark  (P2)

🚧 All scaffolded; not yet exercised end-to-end. Add tests when credentials land.

## 4. Skills  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-401 | Built-in registry loads | `curl /api/v1/skills` | 26+ skills returned; no import errors in core logs | ✅ |
| T-402 | Stock quote | Stock Analyst agent: "AAPL price" | live price from Yahoo `/v8/chart`, NOT `v7/quote` (bug #31, #32) | ✅ |
| T-403 | Web search | "What's the latest news on X" | 200 or 202 response treated as success; results returned (bug #33) | ✅ |
| T-404 | Image analysis | analyze_image on a known-reachable URL (picsum.photos/200) | description returned; not `InvalidParameter: Error downloading` (bug #34) | ✅ |
| T-405 | RAG with embeddings | Upload PDF; ask document question | answer cites the doc; if embeddings 404, BM25 fallback engages (bug #22) | ✅ |
| T-406 | Calendar skills | Receptionist: "Book me Monday 2pm" | calendar updates; conflict detection works | ✅ |
| T-407 | SDR pipeline | Mira agent: "Call next lead" via `outbound_call_batch` with `preview=true` | dry-run output shows top-N leads + script; **no real dial** | ✅ |
| T-408 | Language detection | Multilingual hotline: speak Mandarin | `voice_map` swaps voice to `zh_female_qingxinnvsheng_uranus_bigtts` (bug #59) | 🆕 |
| T-409 | Setup Assistant — voice | `/dashboard/agents/new?mode=voice` | speak "I run a salon"; receptionist template instantiated; agent draft created in DB | ✅ |
| T-410 | Setup Assistant — text+voice hybrid | Start voice; switch to text mid-flow | draft_agent_id survives — both transports see the same state (Session 10) | ✅ |
| T-411 | Skill hot-reload | Edit `~/.openvox/skills/foo.py` while core is running | `watchfiles` triggers reload; new tool appears in next `list_tools` | ⚠ |
| T-412 | Skill TLS routing | grep `httpx.AsyncClient(` in `skills/builtin/**` | zero hits — all calls route through `make_async_client` (bug #31) | ✅ |
| T-413 | recommend_template — multi-keyword match | `recommend_template({"description":"look up an order and start a return"})` | returns `ecommerce-support` with `confidence=0.85`, `recommend_custom=false`, ≥2 hits in reasoning (Session 13) | ✅ |
| T-414 | recommend_template — single-keyword low-confidence | `recommend_template({"description":"search web and return news"})` | returns weak candidate with `confidence=0.4`, `recommend_custom=true` so the setup-assistant prompt routes to the custom path (bug #60 scoring rewrite) | ✅ |
| T-415 | recommend_template — no match | `recommend_template({"description":"agent that reads me horoscopes"})` | returns `template_id=""`, `confidence=0.0`, `recommend_custom=true`, suggests `create_custom_agent` | ✅ |
| T-416 | create_custom_agent skill | Setup Assistant: "create a news reader with web search" → agree → name it | LLM calls `create_custom_agent({"name":...,"skills":["web_search"]})`; new Agent row has `template_id=""` and the right skills; draft_agent_id stashed (Session 13) | ✅ |
| T-417 | Setup Assistant self-heal | After deploying a templates.py change, GET `/api/v1/templates/setup-assistant/singleton` | existing setup-assistant agent's `system_prompt`+`skills`+`greeting` re-sync from current template defaults; core log: `Setup Assistant <id> re-synced from template defaults` (bug #63) | ✅ |
| T-418 | Setup Assistant prompt hygiene | Watch a multi-turn voice session | NO step-number narration ("per step 6"), NO agent_ids spoken, NO raw JSON read aloud; replies <20 words (Session 13) | ⚠ |

## 5. Templates  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-501 | List templates | `/dashboard/templates` | 29 templates listed (8 core + 21 multilingual) | ✅ |
| T-502 | Instantiate (Copy template) | Click "Copy template" on a fresh one | new agent created with template defaults; redirects to `/dashboard/agents/<id>` (Session 12 renamed button) | ✅ |
| T-503 | Auto-suffix duplicate names | Click "Copy template" on the same template 3× | first creates `Acme Support Voice`, then `(2)`, then `(3)` — no confirm dialog, just keep producing distinguishable names (Session 12 superseded the bug #39 dialog) | ✅ |
| T-504 | Voice IDs valid | Each multilingual template's voice_id is in `providers/byteplus/voices.py:VOICES_BY_ID` | all 7 languages map to a real catalogue entry (bug #59) | ✅ |
| T-505 | MCP servers passed through | Instantiate Email Assistant | `agent.mcp_servers` populated from template defaults | ✅ |
| T-506 | Productivity templates | Email Assistant + Calendar Scheduler + Executive Assistant | all 3 visible; each carries the right MCP server config | ✅ |
| T-507 | Auto-suffix on first new name | Delete all copies of Acme Support Voice; click "Copy template" | first copy is `Acme Support Voice` (no suffix) — confirms `_next_available_agent_name` doesn't suffix the first instance (Session 12) | ✅ |

## 6. Dashboard / UX  (P0 = 6.1, P1 = rest)

### 6.1 Smoke — every page loads  (P0)

| ID | Page | URL | Expected | Status |
|---|---|---|---|---|
| T-601 | Landing | `/` | renders without console errors | ✅ |
| T-602 | Overview | `/dashboard` | KPI cards populated | ✅ |
| T-603 | Playground | `/dashboard/playground` | Voice/Text/Audio file/Documents tabs render | ✅ |
| T-604 | Agents | `/dashboard/agents` | list loads | ✅ |
| T-605 | Agent detail | `/dashboard/agents/<id>` | tabs Behaviour/Voice/Skills/Documents/Channels/MCP all render | ✅ |
| T-606 | Templates | `/dashboard/templates` | language filter chips + 29 cards | ✅ |
| T-607 | Schedules | `/dashboard/schedules` | jobs list + run history | ✅ |
| T-608 | Evals | `/dashboard/evals` | stats row + recent runs | ✅ |
| T-609 | Observability | `/dashboard/observability` | session list with cost column | ✅ |
| T-610 | Providers | `/dashboard/providers` | provider grid | ✅ |
| T-611 | Skills | `/dashboard/skills` | skill catalogue | ✅ |
| T-612 | Settings | `/dashboard/settings` | env display | ✅ |

### 6.2 Interactions  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-621 | Topbar search | Type "stock" in search | popover shows matching agents/templates/skills (bug #37) | ✅ |
| T-622 | Publish button | Edit a draft agent → "Publish" | spinner → green toast → badge flips to "published"; status persists on refresh (bug #35) | ✅ |
| T-623 | Delete agent — 8-table cascade | Create agent with docs + recordings + jobs + sessions; Delete; confirm | 200 OK; UI removes row; verify in DB that 8 dependent tables also drained (bug #53) | ✅ |
| T-624 | Bodyless DELETE | Same as T-623; inspect network tab | no `FST_ERR_CTP_EMPTY_JSON_BODY` 400 (bug #54) | ✅ |
| T-625 | SWR cache invalidation | After publish/delete; navigate away + back | list reflects change without manual refresh | ✅ |
| T-626 | No mic leak on navigation | Start voice; switch tab; come back | mic released; no random `user_final` events fired (bug #52) | ✅ |
| T-627 | Voice selector dropdown | Agent detail → Voice tab | renders 41 voices; legacy voice_id shows amber warning | 🆕 |
| T-628 | Test voice button | Click "Test voice" on a known-good voice | plays a sample within 3 s | 🆕 |
| T-629 | Setup Assistant scroll | Long multi-turn conversation | transcript auto-scrolls; respects manual scroll-up (bug #429b2f1) | ✅ |

## 7. Pricing calculator  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-701 | Rate card endpoint | `curl /api/v1/pricing/rates` | every provider has `model_name`, `source_url`, `verified_at`, `notes` (bug #56) | ✅ |
| T-702 | Session cost computed | Click any session row in Observability | drawer shows component breakdown + total; matches manual math | ✅ |
| T-703 | Unit labels rendered | Same drawer | each pill shows unit hint ("$0.0025 / min", "$0.045 / 1k chars") | ✅ |
| T-704 | Rate sources expander | Same drawer | click "Rate sources"; source URLs click through to live pricing pages | 🆕 |
| T-705 | Per-min vs per-char STT dispatcher | Run a session through BytePlus STT then Aliyun (when added) | calculator picks correct pricing model per provider; matrix includes both | ⚠ |
| T-706 | stt_chars persisted | Run a voice session; check observability telemetry | `telemetry.stt_chars > 0` and `stt_chars_estimated: false` (bug #57) | ✅ |
| T-707 | Older session shows estimation flag | Open a session created before stt_chars tracking | amber banner "ASR character count proxied from TTS" (bug #57) | ✅ |
| T-708 | Override file works | Set `OPENVOX_RATES_FILE=/tmp/rates.json` with overrides; restart core | `/pricing/rates` reflects overrides | 🆕 |
| T-709 | What-if matrix sane | Open Q1-style session | cheapest combo isn't current; tip shows savings > $0.001 | ✅ |
| T-710 | BytePlus LLM tier crossover | (NOT IMPLEMENTED) prompt > 128 tokens | should bill at $1.00/$6.00 tier — currently bills at $0.50/$3.00 always (technical debt) | 🆕 |

## 8. Evals  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-801 | Synthetic persona eval | `/dashboard/evals` → New eval → Persona = "Security-paranoid" → Run | run completes with verdict + per-criterion breakdown | ✅ |
| T-802 | Replay recording eval | Save a session as recording; New eval → Replay → pick that recording | run has N turns (NOT 0); judge has dialogue to score (bug #55) | ✅ |
| T-803 | Save session as recording | Observability drawer → "Save as recording" | recording appears in Evals dropdown with correct turn count | ✅ |
| T-804 | Per-criterion display | Inspect eval result | each criterion shows pass/fail badge + judge reasoning (UI bug: shows "0%" per criterion — should be binary) | 🆕 |
| T-805 | Voice WS Transcript writes | Voice session, then `SELECT * FROM transcripts WHERE session_id=...` | rows present per turn (bug #55) | ✅ |
| T-806 | Telegram Transcript writes | Telegram session, same query | rows present (bug #55) | ✅ |

## 9. MCP  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-901 | Catalogue endpoint | `curl /api/v1/mcp/catalogue` | 8 entries (Slack/GitHub/Notion/HubSpot/Salesforce/Stripe/Gmail/Calendar) | ✅ |
| T-902 | Probe valid config | `POST /api/v1/mcp/probe` with a working stdio config | returns `ok: true` and tool list | ⚠ |
| T-903 | Bridged tools appear | Connect Gmail MCP to Email Assistant; start voice session | core logs show `mcp: session built with N bridged tools` | ⚠ |
| T-904 | Teardown on WS close | Same as T-903; close WS | core logs show `mcp_mgr.__aexit__` cleanup; no orphan subprocess | 🆕 |
| T-905 | Catalogue browse modal | Agent detail → MCP tab → "Browse catalogue" | modal pre-fills config form on click | ✅ |

## 10. Scheduler  (P2)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-1001 | Cron job | Create cron `*/2 * * * *` agent_query | first run fires within 2 min; row in `job_runs` | ✅ |
| T-1002 | Interval job | Create interval `30s` skill_run | fires every 30s | ✅ |
| T-1003 | Once job | Create once at T+60s | fires once at scheduled time; state moves to `completed` | ✅ |
| T-1004 | Webhook job | Create webhook job; `POST /api/v1/jobs/webhook/<token>` | job runs; row in `job_runs`; UI shows trigger callout | ✅ |
| T-1005 | Webhook wrong token | POST with wrong token | 200 OK + `received:false` (no enumeration); job NOT run | ⚠ |
| T-1006 | Audio batch | Drop a new WAV into watched folder; trigger audio_batch job | processes only new files (`.openvox_processed` state) | ✅ |
| T-1007 | Outbound call batch | preview=true | dry-run shows leads + script; no real dial | ✅ |
| T-1008 | Scheduler state survives restart | Restart core | jobs reload from DB; in-flight runs not double-fired | ⚠ |
| T-1009 | Simple-mode "Daily 8 AM" | New schedule → Simple mode → Date today, Time 08:00, Repeat Daily → Save | DB row gets `trigger_type=cron`, `trigger_expr="0 8 * * *"`, `next_run_at` = tomorrow 08:00 in agent's tz (Session 12) | ✅ |
| T-1010 | Simple-mode "Doesn't repeat" | Simple mode → future date, 14:30, Repeat: Doesn't repeat | DB: `trigger_type=once`, `trigger_expr="<YYYY-MM-DD>T14:30:00"`; `next_run_at` exact match (Session 12) | ✅ |
| T-1011 | Simple-mode weekly on Saturday | Pick a Saturday date in Simple, 09:00, Repeat Weekly | DB: `trigger_expr="0 9 * * 5"` (APScheduler Mon=0..Sun=6, so Sat=5); `next_run_at` lands on a Saturday — **NOT** Sunday (regression test for bug #60) | ✅ |
| T-1012 | Simple-mode monthly | Date 2026-06-20, 08:00, Repeat Monthly | DB: `trigger_expr="0 8 20 * *"`; next_run_at = 2026-06-20 08:00 (Session 12) | ✅ |
| T-1013 | Simple-mode hourly | Time 09:30, Repeat Hourly | DB: `trigger_expr="30 * * * *"`; next_run_at within an hour at the :30 mark (Session 12) | ✅ |
| T-1014 | Simple ↔ Advanced toggle preserves Advanced state | In New-schedule modal: switch to Advanced, type custom cron, switch to Simple, switch back to Advanced | Advanced field retains the user's custom cron — Simple toggle is non-destructive (Session 12) | ⚠ |
| T-1015 | Edit defaults to Advanced | Click Edit on an existing cron schedule | modal opens with Advanced selected, NOT Simple — avoids lossy reverse-translation of arbitrary cron (Session 12) | ✅ |

## 11. Voice catalogue  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-1101 | `/providers/voices` returns full catalogue | curl endpoint | 41 BytePlus voices + curated OpenAI/ElevenLabs/Cartesia (bug #59) | ✅ |
| T-1102 | `is_known(voice_id)` validation | unit-call from Python REPL inside core | returns True for catalogue entries, False for `multilingual_v2_*` | ✅ |
| T-1103 | All template voices validated | grep `voice_id` in `templates.py`; cross-ref with `VOICES_BY_ID` | every ID exists in the catalogue OR is the empty string | 🆕 |
| T-1104 | Test voice button — happy path | Pick Tim → Test voice | sample plays via Web Audio API; uses `api.synthesize()` not relative URL (bug #65 regression test) | ⚠ |
| T-1105 | Test voice button — unactivated | Pick a voice user doesn't have activated → Test voice | red error message surfaces the BytePlus `code=55000000` verbatim — propagated through `api.synthesize` Error.message | 🆕 |
| T-1106 | Test voice button — no Next.js 404 | Pick any voice → Test voice → if it fails, error toast must NOT be `<!DOCTYPE html>...` (the Next.js 404 shell). Should be the backend's plain error text | ✅ (bug #65) |

## 12. Storage / persistence  (P1)

| ID | Test | Steps | Expected | Status |
|---|---|---|---|---|
| T-1201 | Audio upload to local FS | Playground → Audio file tab → upload .wav | stored under `DATA_DIR/recordings/` (DATA_DIR not OPENVOX_DATA_DIR, bug fixed in Session 9) | ✅ |
| T-1202 | Recording → TOS / S3 (when configured) | Set storage backend = byteplus_tos; upload | object lands in bucket; signed URL retrievable | ⚠ |
| T-1203 | RAG store survives restart | Upload PDF; restart core; query | answer still returns; chunks present in DB | ✅ |

---

## New tests proposed (not yet run)

Tests marked 🆕 above. The biggest gaps:

- **`clean_for_tts` golden samples** (T-221 to T-225) + Session-13
  text-helpers (T-228, T-230, T-413-T-418). Right now we test each
  pattern by hand once, and Session 13 ran a 27-case truth-table
  via an ad-hoc `/tmp/voice_fix_tests.py`. **Promote both into a
  permanent pytest harness:**
  ```bash
  packages/core/tests/test_text_helpers.py    # ReasoningStripper, sanitize_user_final, clean_for_tts
  packages/core/tests/test_setup_skills.py    # recommend_template scoring + create_custom_agent
  ```
  with `INPUT → EXPECTED` pairs. Regress every commit. This is now
  load-bearing — voice quality depends on these filters being correct.
- **LLM tier crossover** (T-710). The pricing calculator quietly assumes the BytePlus `[0,128]` LLM tier. For multi-turn sessions with growing history, every turn after the first probably crosses 128 input tokens and gets billed double. We have the `prompt_tokens` value in `llm_usage` events — wire it through.
- **Voice catalogue regression** (T-1103). Add a CI check that fails the build if any template `voice_id` is missing from `VOICES_BY_ID`. Prevents another bug #59.
- **MCP teardown** (T-904). Subprocess leaks would only show up after many sessions — easy to miss until a server runs out of file descriptors.
- **Webhook token security** (T-1005). Disabled-or-wrong-token cases must return 200 with `received:false`, NOT 401 — verify nobody regresses this into a 401 (which would let an attacker enumerate valid tokens).
- **Scheduler restart safety** (T-1008). APScheduler in-memory state vs DB on restart is the kind of thing that breaks at the worst time.

---

## Maintenance

- After landing a fix for a CLAUDE.md bug, add a row here that explicitly tests
  the regression. Don't trust the in-prose summary in CLAUDE.md alone.
- When a row stays ⚠ or 🆕 for more than two sessions, either: (a) run it and
  flip to ✅, or (b) automate it. Stale unverified rows are noise.
- A row should have **one** test step set, **one** expected outcome. If a test
  needs branching (e.g. "with embeddings working" vs "BM25 fallback"), split
  into two rows.
- **Refresh `verified_at` annotations** when you rerun a P0/P1 row — eventually
  the dashboard can render a "last verified" column per test.
