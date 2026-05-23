# Planning — Sessions 15+ (Path B refactor + native install)

Created end of Session 14 conversation (2026-05-23), committed at the start of Session 15.

> **Background.** Sessions 12-14 shipped UX polish + voice-pipeline quality + Telegram tunnel.
> User then asked whether OpenVox has a viable niche given OpenClaw exists.
> Research showed: OpenClaw is a personal-assistant product (different shape), and the
> real OSS voice-agent competition is Dograh / LiveKit Agents / Pipecat / TEN — all
> better-resourced. OpenVox's defensible angle is **multilingual depth + non-tech
> onboarding** (closer to OpenClaw's bar) — NOT a generic "OSS voice agent framework"
> positioning.
>
> Two concrete corrections came out of that discussion:
> 1. **Vendor-neutral marketing** (no BytePlus-led positioning publicly — already
>    shipped in commit `7c25034`).
> 2. **Close the non-tech onboarding gap** — today OpenVox requires Docker + `.env`
>    editing + ngrok signup, which OpenClaw avoided. This plan addresses that.

---

## Strategic context

Three decisions from the Session 14 conversation that shape this plan:

1. **Same project, dual-mode.** Personal-CLI install and production-Docker install
   share one codebase, one community, one docs site. Industry pattern (Ollama,
   Streamlit, Jupyter, Postgres). Forking at this stage is a known anti-pattern.

2. **Match OpenClaw's bar, not exceed it (yet).** OpenClaw doesn't ship signed
   double-click installers either — they ship a one-command install (curl-bash +
   npm). Phase 4 below matches that bar with $0 ongoing cost (no code-signing
   certs). Signed native installers deferred until traction justifies the spend.

3. **Skip WeChat personal QR.** Wechaty + free puppets are unreliable; PadLocal is
   paid (~$25/mo); WeChat actively bans personal accounts using automation as of
   2026. The reputational cost of "I tried OpenVox and lost my WeChat" stories
   outweighs the feature value. WeChat Work (already supported, official API)
   stays the supported path.

---

## At a glance

| Phase | Goal | Calendar | Status |
|---|---|---|---|
| **1** | Strip stack so it runs without Docker | ~2 weeks | Pending |
| **2** | Channel adapters without public URLs (Telegram polling + WhatsApp QR) | ~1.2 weeks | Pending |
| **3** | First-run wizard for API keys + template + channel | ~1 week | Pending |
| **4** | Four install paths (pip / curl-bash / brew / winget) + daemon mode | ~2 weeks | Pending |
| **Total** | | **~6 weeks single-track**, +20-30% real-world buffer | |

Each phase is independently shippable as its own PR.

---

## Phase 1 — Strip stack to no-Docker

**Goal:** `pip install openvox && openvox run` works on a clean machine, no external
services. Existing Docker mode preserved via env-var gating.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 1.1 | **Storage abstraction** — pluggable backend | NEW `packages/core/openvox/storage/base.py` (ABC)<br>NEW `packages/core/openvox/storage/sqlite.py`<br>MOD `packages/core/openvox/storage/postgres.py` (refactor existing)<br>MOD `packages/core/openvox/db/__init__.py` (factory) |
| 1.2 | **Queue abstraction** — in-process default | NEW `packages/core/openvox/queue/base.py`<br>NEW `packages/core/openvox/queue/in_process.py` (asyncio.Queue + APScheduler)<br>NEW `packages/core/openvox/queue/redis.py` (lift existing) |
| 1.3 | **Merge Node gateway into FastAPI core** — biggest risk | NEW FastAPI WS endpoint replacing `packages/server/src/ws/voice.ts`<br>NEW middleware: rate-limit via `slowapi`, simple bearer-token auth<br>KEPT `packages/server/` (Docker mode still uses it) |
| 1.4 | **Dashboard static build** — Next.js → static, served by FastAPI | MOD `apps/dashboard/next.config.js` (enable `output: 'export'`)<br>NEW FastAPI route mounts `apps/dashboard/out/` at `/dashboard/*`<br>MOD `apps/dashboard/src/lib/api.ts` (same-origin in CLI mode) |
| 1.5 | **CLI scaffold** | NEW `packages/core/openvox/cli/__init__.py` (typer-based)<br>NEW `packages/core/openvox/cli/commands/run.py`<br>MOD `packages/core/pyproject.toml` (`console_scripts` entry) |
| 1.6 | **Default file locations** | `~/.openvox/openvox.db` (SQLite)<br>`~/.openvox/secret.key` (machine key, 0600)<br>`~/.openvox/logs/openvox.log` |
| 1.7 | **Dual-mode env switch** | `DATABASE_URL` unset → SQLite, set → Postgres<br>`REDIS_URL` unset → in-process, set → Redis<br>`OPENVOX_MODE` defaults `personal`, alt `production` |

### Sub-tasks (chronological)

1. **(3 days) Architecture spike** — branch-only prototype: SQLite + FastAPI-only stack.
   No production code touched. Goal: confirm WebSocket port is feasible, surface unknowns.
2. **(1 day) Storage backend** — interface + SQLite implementation + Postgres refactor.
   Verify all 8 cascading deletes from bug #53 work in both.
3. **(1 day) Queue backend** — interface + in-process queue. Scheduler still fires
   (T-1001 passes).
4. **(3 days) WebSocket port** — Node `voice.ts` → Python FastAPI WS endpoint.
   Verify Sessions 8/11 fixes survive.
5. **(1 day) Dashboard static build** — Next.js `output: 'export'`, serve from FastAPI,
   audit relative-URL bugs (similar to bug #65).
6. **(1 day) CLI scaffold + `openvox run`** — basic typer CLI, `webbrowser.open()` on
   ready.
7. **(1 day) Run full TESTPLAN in both modes** — fix breakage.
   Critical: T-201 (voice), T-321 (Telegram), T-1004 (webhook), T-501 (templates).
8. **(0.5 day) Doc the new mode** — NEW `docs/install-cli.md`, MOD `README.md`.

### Verification

- ✅ Clean Mac/Linux: `pip install -e packages/core && openvox run` → dashboard at
  `http://localhost:8000/dashboard`
- ✅ Existing `docker compose up` continues to work
- ✅ Full TESTPLAN P0 + P1 passes in BOTH modes
- ✅ 27-case truth-table from Session 13 still passes (promote to
  `packages/core/tests/test_text_helpers.py` as a side effect)

### Risks

| Risk | Mitigation |
|---|---|
| WebSocket port loses Sessions 8/11 fixes | Spike-first (sub-task 1); careful side-by-side testing |
| SQLite concurrency on multi-user | Acceptable — multi-user = production = use Postgres |
| `_ADDITIVE_COLUMNS` shim needs Alembic migration | Defer to Phase 5 unless blocks dual-mode |
| Static build loses dev hot-reload | Keep Next.js dev server for Docker mode; static only for CLI |

---

## Phase 2 — Channel adapters without public URLs

**Goal:** Telegram and WhatsApp work locally with no ngrok / public URL config.
WeChat personal explicitly NOT supported.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 2.1 | **Telegram long-polling** | MOD `packages/core/openvox/telephony/telegram.py` — dispatch on `OPENVOX_TELEGRAM_MODE` env (`polling` default, `webhook` opt-in)<br>Background polling task spawned at startup |
| 2.2 | **Dashboard Telegram tab** — polling-friendly | MOD `apps/dashboard/src/app/dashboard/agents/[id]/page.tsx` Channels section<br>Polling: BotFather token + "Start" button (no webhook URL)<br>Webhook (production): existing flow behind toggle |
| 2.3 | **WhatsApp Personal adapter** — QR via whatsapp-web.js | NEW `packages/core/openvox/telephony/whatsapp_personal.py` (Python orchestrator)<br>NEW `packages/whatsapp_personal_bridge/` (Node subprocess running whatsapp-web.js, local HTTP IPC)<br>Dashboard renders QR returned by subprocess |
| 2.4 | **Dashboard WhatsApp Personal tab** | NEW tab in Channels section<br>Big warning: "Uses unofficial WhatsApp Web protocol. Account may be banned. Test number only."<br>QR display + "Connected as +X" |
| 2.5 | **WeChat: position WeChat Work as the path** | MOD `docs/extending.md` — explain why personal WeChat isn't bundled (ban risk)<br>No code change |
| 2.6 | **Drop ngrok from roadmap** | MOD `docs/PLANNING_NEXT.md` — remove "Built-in ngrok integration"<br>`--profile tunnel` kept for production WhatsApp Business / Twilio |

### Sub-tasks

1. **(0.5 days)** Refactor `telephony/telegram.py` to dispatch on mode env var.
2. **(1 day)** Test Telegram polling end-to-end; verify T-321/T-322/T-323/T-324 pass.
3. **(2 days)** WhatsApp Personal: Node subprocess wrapper for whatsapp-web.js + IPC.
4. **(1 day)** Dashboard UI for WhatsApp Personal tab.
5. **(0.5 days)** Doc updates.
6. **(1 day)** Test WhatsApp Personal end-to-end with real test number.

### Verification

- ✅ Fresh install, no ngrok, Telegram tab → paste BotFather token → bot replies in 2s.
- ✅ WhatsApp Personal: connect → QR → scan → bot replies.
- ✅ Webhook-mode Telegram + Business WhatsApp still works (production users unaffected).

### Risks

| Risk | Mitigation |
|---|---|
| whatsapp-web.js breaks on Meta protocol updates | Pin version; document upgrade |
| Polling misses messages during downtime | Acceptable (Telegram queues 24h) |
| WhatsApp Personal bans → reputational hit | Mandatory prominent warning in UI |

---

## Phase 3 — First-run wizard

**Goal:** A non-technical user opens the dashboard after install and is guided
through API keys → template → playground in <5 minutes, never touching `.env`.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 3.1 | **First-run detection** | MOD `apps/dashboard/src/app/dashboard/layout.tsx` — check `/api/v1/admin/setup_complete`, redirect to `/dashboard/setup` if false |
| 3.2 | **Welcome step** | NEW `apps/dashboard/src/app/dashboard/setup/page.tsx`<br>Branding + CTA<br>Two entry points: "Build by clicking" (wizard) or "Build by voice" (existing Setup Assistant) |
| 3.3 | **API keys step** | NEW `apps/dashboard/src/app/dashboard/setup/keys/page.tsx`<br>Provider picker; form posts to NEW `POST /api/v1/admin/setup/keys` |
| 3.4 | **Encrypted key store** | NEW `packages/core/openvox/storage/secrets.py` — envelope encryption using `~/.openvox/secret.key`<br>NEW DB table `provider_keys` |
| 3.5 | **Per-provider key validation** | MOD provider modules — each gets `validate_key()`; wizard shows green/red inline |
| 3.6 | **Template picker** | NEW `apps/dashboard/src/app/dashboard/setup/template/page.tsx`<br>Reuses existing template card components |
| 3.7 | **Test step** | Auto-redirect to playground with new agent + prefilled "Hi"<br>No new code; URL parameters only |
| 3.8 | **Optional channel step** | Inline Telegram polling: paste BotFather token, Connect<br>Inline WhatsApp QR: same as Phase 2 tab<br>Both skippable |
| 3.9 | **CLI wizard** (`openvox onboard`) | NEW `packages/core/openvox/cli/commands/onboard.py` — terminal version via `typer.prompt`, for headless / SSH |

### Sub-tasks

1. (0.5 days) First-run detection + welcome
2. (1 day) Encrypted key store backend
3. (1 day) API keys form + per-provider validation
4. (0.5 days) Template picker
5. (0.5 days) Channel step inline
6. (1 day) CLI version (`openvox onboard`)
7. (0.5 days) Polish, error states
8. (0.5 days) E2E Playwright test

### Verification

- ✅ Clean SQLite DB → dashboard auto-redirects to `/dashboard/setup`
- ✅ Wizard completes in <5 min with no terminal
- ✅ Provider keys persist (encrypted in SQLite)
- ✅ Existing user (has agents) → no wizard
- ✅ `openvox onboard` from terminal equivalent

### Risks

| Risk | Mitigation |
|---|---|
| Encrypted key file leaks via logs | 0600 perms; explicit redact in logging filter |
| Key validation hits real API costs | Cheapest endpoint per provider; cache 30s |
| First-run detection wrong | Cookie + DB both checked; explicit "dismiss" option |

---

## Phase 4 — Native install + daemon mode

**Goal:** Install via any of four free channels (pip / curl-bash / brew / winget),
then start foreground (`run`) or as daemon (`start`).

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 4.1 | **CLI commands** | NEW `packages/core/openvox/cli/commands/{run,start,stop,status,restart,onboard,logs,version}.py` |
| 4.2 | **macOS daemon** | NEW `packages/core/openvox/cli/daemon/launchd.py` — generates `~/Library/LaunchAgents/com.openvox.daemon.plist`; wraps `launchctl` |
| 4.3 | **Linux daemon** | NEW `packages/core/openvox/cli/daemon/systemd.py` — generates `~/.config/systemd/user/openvox.service`; wraps `systemctl --user` |
| 4.4 | **Windows daemon** | NEW `packages/core/openvox/cli/daemon/windows_service.py` — uses bundled `nssm.exe`; wraps `sc` |
| 4.5 | **Path A: PyPI** | MOD `packages/core/pyproject.toml` — `console_scripts`, `package_data` (dashboard statics), optional extras (`openvox[whatsapp]`, `openvox[postgres]`) |
| 4.6 | **Path B: install.sh** | NEW `scripts/install.sh` — OS/arch detect, Python check, pip install, PATH symlink, prompt to start |
| 4.7 | **Path B hosting** | GitHub Pages on `openvox.ai` (or `amznsri.github.io/openvox`) |
| 4.8 | **Path C: Homebrew tap** | NEW separate repo `amznsri/homebrew-openvox`<br>NEW `Formula/openvox.rb` |
| 4.9 | **Path D: WinGet manifest** | NEW `winget-manifests/OpenVox/...` YAMLs<br>PR to `microsoft/winget-pkgs` |
| 4.10 | **Release pipeline** | NEW `.github/workflows/release.yml` — on tag: PyPI publish, Homebrew formula bump, WinGet manifest update, GitHub Release with install.sh checksum |
| 4.11 | **Auto-open browser** | MOD `cli/commands/run.py` + `start.py` — `webbrowser.open()` after readiness |
| 4.12 | **Update README + landing** | MOD `README.md` — "Quick install" with all 4 paths |

### Sub-tasks

1. (1 day) CLI commands skeleton (`run`/`start`/`stop`/`status`/`restart`/`onboard`/`logs`/`version`)
2. (1 day) macOS launchd lifecycle
3. (1 day) Linux systemd lifecycle
4. (1.5 days) Windows Service via nssm
5. (1 day) PyPI packaging + TestPyPI → real PyPI publish
6. (0.5 days) install.sh + hosting
7. (1 day) Homebrew formula + own tap
8. (0.5 days) WinGet manifest + submit PR
9. (1 day) GitHub Actions release workflow
10. (0.5 days) Documentation
11. (1 day) E2E on clean macOS / Ubuntu / Windows VMs (each path)

### Verification matrix

| Path | Test machine | Expected |
|---|---|---|
| A: pip | Clean Docker python:3.11 | `pip install openvox && openvox start && curl localhost:8000/health` → 200 |
| B: install.sh | Clean Ubuntu 24.04 | `curl -fsSL openvox.ai/install.sh \| bash` → daemon running, browser open |
| C: brew | Clean macOS Sequoia | `brew tap amznsri/openvox && brew install openvox && openvox start` |
| D: winget | Clean Windows 11 | `winget install OpenVox.OpenVox && openvox start` |

### Risks

| Risk | Mitigation |
|---|---|
| PyPI name `openvox` taken | Reserve immediately (before Phase 1 if possible) |
| Homebrew-core submission slow | Own tap initially; submit to core when traction |
| AV false-positives | Pure Python (no PyInstaller). N/A for paths A-D |
| nssm.exe bundling adds 300KB | Acceptable; standard practice |
| Multi-OS CI failures | GitHub Actions matrix; test before each release |

---

## Cross-cutting work

### Testing strategy

After **each phase**: run TESTPLAN P0 + P1 in **both** Docker AND CLI modes.

Promoted from `/tmp` to permanent pytest (carried-over follow-up from Session 13):
- NEW `packages/core/tests/test_text_helpers.py` — 27-case truth table for
  `ReasoningStripper` + `sanitize_user_final` + `clean_for_tts`.
- NEW `packages/core/tests/test_setup_skills.py` — `recommend_template` scoring +
  `create_custom_agent`.

New per phase:
- Phase 1: SQLite-vs-Postgres parity tests.
- Phase 2: Telegram polling vs webhook parity; WhatsApp Personal QR mock.
- Phase 3: Playwright E2E for wizard.
- Phase 4: CI install-test on each OS.

### Documentation deliverables

- Phase 1: NEW `docs/install-cli.md`; MOD `docs/architecture.md` for dual-mode.
- Phase 2: MOD `docs/channels.md` — polling vs webhook decision matrix.
- Phase 3: NEW `docs/onboarding.md` — wizard walkthrough.
- Phase 4: NEW `docs/install.md` — all four paths consolidated.
- Final: MOD `README.md` to lead with "Personal install" + "Production install".

### Decision points (open at planning time)

1. **PyPI name reservation** — register `openvox` on PyPI before Phase 1 starts. Free.
2. **Homebrew tap repo name** — `amznsri/homebrew-openvox` (Homebrew convention).
3. **install.sh hosting** — GitHub Pages on `openvox.ai` or `amznsri.github.io/openvox`.
4. **Daemon log location** — `~/.openvox/logs/` cross-platform (recommended) vs
   OS-canonical paths.

---

## Sequencing

```
Phase 1 (must come first — provides storage abstraction + CLI scaffold)
   │
   ├── Phase 2 ─┐  (can overlap; different files)
   │           │
   └── Phase 3 ─┤  (can overlap; different files)
               │
               └── Phase 4 (uses everything from 1-3)
```

Each phase committable as one or two PRs. Project shippable after every phase.

---

## Shippable milestones

| After phase | What's possible |
|---|---|
| 1 | Developers can `pip install openvox` and run locally without Docker |
| 2 | Non-tech users connect Telegram without any URL config |
| 3 | Non-tech users complete first agent in <5 min, no `.env` editing |
| 4 | Install via four channels (pip/curl/brew/winget), runs as daemon |

---

## Out of scope (deferred to later sessions)

- **Path E: signed double-click installer** (`.dmg`/`.msi`/`.deb`). Requires
  ~$300-600/year code-signing certs. Deferred until traction justifies. Reference
  pattern: Ollama added these only after community demand.
- **Mobile companion apps** (iOS / Android) like OpenClaw's. Different native build,
  no Electron carryover.
- **Server-side echo cancellation for VAD barge-in** (Session 13 deferred). Client-
  side Stop button + browser stop-word listener are good enough until non-browser
  channels demand true server-side barge-in.
- **WeChat personal QR adapter via Wechaty/PadLocal.** Skipped per Session 14
  decision (account ban risk).
- **Cloud-hosted multi-tenant mode + OAuth.** Long-standing carry-forward; not
  blocking this work.

---

## Handoff notes for future Claude sessions

If you (Claude, future) pick up Phase 1 cold:

1. Read this doc top-to-bottom.
2. Skim `CLAUDE.md §8` for bugs #47-65 (recent gotchas: gateway proxy rules,
   WS framing, FK cascades, reasoning tags, etc.).
3. Check current branch: `git branch --show-current`. Phase 1 work happens on
   `phase1-spike` first, then `phase1-implementation` for the real refactor.
4. Run TESTPLAN P0 in current state to baseline before changes.
5. Start with sub-task 1 (architecture spike) — don't touch production code
   in the spike phase. Goal is risk discovery, not feature delivery.

Open question to verify in the spike: **does FastAPI's WebSocket implementation
handle the exact frame sequencing Sessions 8/11 fixed in the Node gateway?**
If not, the WebSocket port is more dangerous than estimated and the plan needs
re-pricing.
