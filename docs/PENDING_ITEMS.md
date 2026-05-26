# Pending items — post-Session 18

Snapshot of every known-but-not-shipped item at the close of
Session 18 (v0.2.12). Maintained as a session-resilient queue so a
fresh session can pick up without re-deriving the list.

Categories follow the "honest read" framing from the
pending-queue conversation:

  - **A** — Open + scoped + ready to start
  - **B** — Explicitly skipped this session (operator's call)
  - **C** — Blocked on external dependencies (credentials, Google,
    Meta, Twilio, etc.)
  - **D** — Small infrastructure follow-ups noted in CLAUDE.md but
    never spec'd
  - **E** — Larger roadmap placeholders with no plan yet

Anything that gets picked up should move out of this file into a
dedicated `docs/PLANNING_SESSION<N>.md` like Session 18 did.

---

## A. Open + scoped + ready to start

| ID | Item | Effort | Notes |
|----|------|--------|-------|
| A.PR-B | **Phase 3 PR-B** — S2S orchestrator branch + Voice-tab toggle + pricing entry | ~3-4 hrs | PR-A merged in v0.2.10. PR-B needs the operator's OpenAI API key for live validation against Realtime — protocol has subtleties (audio framing, interrupt timing, tool-call sequencing) that only surface against the real server. Skipping live testing = shipping v0.2.6-style hotfix bait. |

---

## B. Explicitly skipped this session

| ID | Item | Note |
|----|------|------|
| B.4 | **Phase 4** — GTM polish (README rewrite from deck, landing-page redesign, fresh screenshots, 2-min demo video) | Operator can revisit any time; not urgent. |

---

## C. Blocked on external dependencies

| ID | Item | Why blocked |
|----|------|-------------|
| C.1.7 | **Phase 1.7** — submit Google OAuth app for verification | 1-2 wk Google turnaround; operator action required (verification has to be filed by the project owner). |
| C.twilio | Twilio Media Streams ↔ pipeline bridge | Needs a real Twilio account + phone number for live test. |
| C.wa | WhatsApp Business inbound | Needs a Meta-approved phone number ID. |
| C.wechat | WeChat Work / Lark audio bridges | Needs test workspace credentials. |

---

## D. CLAUDE.md infrastructure follow-ups (never spec'd)

| ID | Item | Effort |
|----|------|--------|
| D.9v2 | **D9-v2** — promote soft FKs (eval_runs.agent_id, scheduled_jobs.agent_id, recordings.source_agent_id, document_chunks.agent_id) to hard FKs with appropriate ON DELETE semantics (CASCADE vs SET NULL). Recording is debatable — audit-trail-y, may want SET NULL. | ~1.5 hr (migration + tests + per-FK semantic decisions) |
| D.cascade-retire | **Retire in-route manual cascade** in `agents.py:delete_agent` once D9's DB-level cascade has shipped + been observed in production. The chain shrinks from ~30 lines to `await s.delete(a)`. | ~30 min + 1 release of confidence-building |
| D.orjson | Silence orjson dylib `Failed to fix install linkage` warning in the Homebrew formula (CLAUDE.md #83 family). | ~30 min |
| D.tg-out | **Telegram outbound channel** — today only inbound (polling/webhook) is wired. An outbound path means an agent can proactively message a Telegram chat (cron / event-triggered). Pattern: `tg.send_text(token, chat_id, body)` is already in `openvox/telephony/telegram.py`; needs a route + dashboard wiring + scheduled-job kind. | ~2 hrs |
| D.hatch-version | Make `pyproject.toml` read `version` dynamically from `openvox/__init__.py` via hatchling's `[tool.hatch.version]` dynamic config. Replaces today's two-file sync test (test_version_sync.py) with a single source. | ~30 min |
| D.health-route-cleanup | (Minor) The pre-D9 `/health` route was the canary for the version-sync drift — once D.hatch-version lands, the test_version_sync regex parser can go too. | ~10 min, ride along with D.hatch-version |

---

## E. Larger roadmap placeholders — listed in CLAUDE.md §7, never planned

These are aspirational. Picking any up should start with a
planning-doc session like `docs/PLANNING_SESSION18.md` was.

| ID | Item | Rough framing |
|----|------|---------------|
| E.voice-clone | Voice cloning UI | Provider matrix supports it via ElevenLabs / BytePlus voice cloning APIs; no dashboard surface today. |
| E.translate | Live interpretation / real-time translation | Bidirectional streaming pipeline + dual-language voice synthesis. |
| E.podcast | Voice podcast generation (two-speaker) | One-shot generator → multi-voice TTS with prosody control. |
| E.rtc | BytePlus RTC client SDK on the dashboard | Server-side token issuance exists; client never wired because direct-WS already works for the playground. Browser-level RTC unlocks low-latency P2P + group rooms. |
| E.multi-tenant | Multi-tenant / cloud-hosted mode | OAuth + JWT scaffolding already exists, gated behind `OPENVOX_AUTH=disabled`. Full enable means user table, per-user data isolation, billing, etc. — a big effort. |
| E.winget-pyinstaller | PyInstaller-based WinGet path (CLAUDE.md #83) | Kill-switched until a self-contained `.exe` + code-signing cert can be sorted (~$300-400/yr cert). |
| E.playwright | Comprehensive Playwright dashboard tests | Placeholder in `test.yml` line 142-149. HTTP-only e2e covers the v0.1.7-class bugs faithfully; Playwright deferred until a visual/interaction regression an HTTP test can't catch surfaces. |

---

## How to use this file

1. **Picking something up**: move the row OUT of this file and into a new
   `docs/PLANNING_SESSION<N>.md` (or a similar dedicated planning doc).
   The pending file should always reflect the unstarted-state.
2. **Adding something new**: append it under the right bucket. If
   you're not sure, **D** is the default for "small fix I noticed";
   **E** for "feature idea, no plan."
3. **Closing something blocked**: when an external dependency
   unblocks (e.g. Google OAuth verification approved), the item
   either moves to **A** (now ready to start) or gets closed
   directly into shipped code with a CLAUDE.md note.
4. **Operator skips**: items in **B** stay there indefinitely
   until the operator says "ok, do it." They don't decay.
