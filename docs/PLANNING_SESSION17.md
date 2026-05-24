# Planning — Session 17 (production-trustworthy: tests + migrations + install matrix closure)

Created at the start of Session 17 (2026-05-24, post-v0.1.8 shipping
the dashboard-bundled-in-wheel + secrets-bridged-to-providers flow).

> **Background.** Sessions 15-16 executed Phases 1-4 of
> [`PLANNING_SESSION15.md`](./PLANNING_SESSION15.md) — Docker-stack
> diet, channel adapters, first-run wizard, native install + daemon
> mode. v0.1.8 (the current release) is the first version where
> `pipx install openvox-core && openvox start && open browser` works
> end-to-end. 7 bug-fix versions (v0.1.1 → v0.1.7) were burned along
> the way because most fixes were validated by API checks, not real
> browser click-throughs.
>
> Session 17's premise: lock down a foundation that makes every
> future change cheaper. Specifically, the class of bugs that broke
> v0.1.7 (provider keys not flowing from wizard to providers; mount
> serving wrong file; static export missing index.html files) would
> have been caught by a single ~80-line Playwright walk-through.
> They weren't, because that test didn't exist. Build the foundation,
> then every Session-18+ feature lands with a safety net.

---

## Strategic context

Three decisions that shape this plan:

1. **No new product features.** Session 17 is foundation, not
   shipping. Items in the carry-forward backlog (live interpretation,
   voice podcast, MCP catalogue, S2S, cloud-hosted multi-tenant)
   stay deferred. They'll move faster afterwards because changes
   won't silently break the wizard / providers / install paths.

2. **PyInstaller-for-WinGet deferred.** Decision recorded end of
   Session 16: skip until a real Windows non-tech user reports
   bouncing off the pip install. Path D remains "use `pip install`
   on Windows" in docs. Building speculatively before any signal
   would cost ~1-2 days that's better spent on tests.

3. **Real users still ~0.** Adoption signal hasn't begun. This is
   the right window for slow foundation work — nobody is waiting on
   a fix. Once users arrive, every refactor competes with their
   bug reports.

---

## At a glance

| Phase | Goal | Calendar | Status |
|---|---|---|---|
| **1** | Real pytest suite — promote `/tmp` + Phase 4 mocks into a proper harness | ~3 days | ✓ Done (PR #3) |
| **2** | Playwright E2E walk: install → wizard → create agent → test voice → verify audio | ~2 days | ✓ Done (PR #4) — HTTP-only deviation; Playwright deferred |
| **3** | Alembic migrations replace `Base.metadata.create_all()` | ~2 days | ✓ Done (PR #6) |
| **4** | Provider error messages mention the wizard, not just `.env` | ~1 day | ✓ Done (PR #5) |
| **5** | Install matrix closure — Homebrew sha256 + macOS/Linux/Windows daemon smoke tests | ~2 days | **Pending** |
| **6** | CI matrix hardening — multi-OS, coverage reporting, PR comments | ~1 day | **Pending** |
| **Total** | | **~11 days single-track**, +20-30% real-world buffer → ~2 weeks | **4 of 6 done** |

PRs stacked, merge order: 3 → 4 → 5 → 6. v0.2.0 release planned
after merge.

Each phase committable as 1-2 PRs. Project shippable after every phase
(no half-states that brick the daemon).

---

## Phase 1 — Real pytest suite

**Goal:** `pytest` at the repo root runs against `packages/core/` and
exits 0. Coverage of the core text-processing + secrets + daemon
modules ≥ 70%. CI fails any PR that breaks a test.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 1.1 | **Promote 27-case truth table** from Session 13's `/tmp` script | NEW `packages/core/tests/test_text_helpers.py` |
| 1.2 | **Secrets module tests** — encrypt/decrypt round-trip, key rotation, wizard-stored-then-retrieved | NEW `packages/core/tests/test_secrets.py` |
| 1.3 | **Hydration test** — populate encrypted store, run `_hydrate_secrets_into_env`, verify env populated + settings cache busted (catches bugs #77 + #78) | NEW `packages/core/tests/test_secrets_hydration.py` |
| 1.4 | **Setup-skills tests** — `recommend_template` scoring + `create_custom_agent` (carry-forward from Session 13) | NEW `packages/core/tests/test_setup_skills.py` |
| 1.5 | **Provider unit tests** — mock httpx, verify each provider builds the right request given a key | NEW `packages/core/tests/test_providers/test_byteplus_tts.py` and 4 more |
| 1.6 | **Pytest fixtures + conftest** — temp data dir, sqlite-in-memory, mock HTTP transport | NEW `packages/core/tests/conftest.py` |
| 1.7 | **Coverage config** + reporting | MOD `packages/core/pyproject.toml` ([tool.pytest.ini_options], [tool.coverage]) |

### Sub-tasks

1. **(0.5 days)** Set up conftest.py with the three core fixtures
   (temp openvox home, isolated sqlite, mock httpx). Establish the
   patterns the rest of Phase 1 builds on.
2. **(0.5 days)** Sub-task 1.1 — port the 27-case truth-table from
   the `/tmp` script into `test_text_helpers.py`. Verify all pass.
3. **(0.5 days)** Sub-task 1.3 — write the hydration test specifically
   targeting bugs #77 + #78. This is the highest-value single test.
4. **(0.5 days)** Sub-task 1.2 — secrets module round-trip + edge cases.
5. **(0.5 days)** Sub-task 1.5 — one provider as a template, then
   replicate the pattern for the other 4 (OpenAI, Anthropic, Gemini,
   ElevenLabs).
6. **(0.5 days)** Sub-task 1.4 — setup-skills tests.
7. **(0.5 days)** Coverage gates + pytest markers (slow, integration,
   network) + CI integration.

### Verification

- `pytest` at `packages/core/` exits 0 on a clean checkout
- Coverage ≥ 70% on `openvox/secrets.py`, `openvox/cli/daemon/`,
  `openvox/api/app.py`'s hydration helper, `openvox/skills/builtin/`
- Re-running locally: `<5 seconds` for the unit-test pass

### Risks

| Risk | Mitigation |
|---|---|
| Tests are flaky because of sqlite locking under pytest-asyncio | Use `pytest-asyncio` with `--asyncio-mode=auto`; use `sqlite:///:memory:` per-test fixture |
| Mocking httpx for providers becomes an unmaintainable mess | Use `respx` library (already common); one shared fixture in conftest |
| Test file paths conflict with the `packages/core/openvox/` package layout | Use `pythonpath = ["packages/core"]` in `pyproject.toml`'s `[tool.pytest.ini_options]` |

---

## Phase 2 — Playwright E2E walk

**Goal:** A single test spawns the daemon in a subprocess, opens a
real headless Chromium against `http://localhost:8000/`, walks the
first-run wizard, creates an agent, hits the test-voice endpoint,
asserts the response is 200 + `audio/pcm`. Runs in CI on every PR.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 2.1 | **Playwright fixture** — spawn `openvox run --no-browser --port <random>` in a subprocess; wait for `/health`; tear down after test | NEW `packages/core/tests/conftest_e2e.py` |
| 2.2 | **The walk** — wizard → enter test BytePlus key → land on dashboard → templates → instantiate → assert agents/edit page loads → click "Test voice" → assert ≥1KB audio bytes returned | NEW `packages/core/tests/e2e/test_first_run.py` |
| 2.3 | **CI integration** — install playwright browsers in workflow, run on Ubuntu runner only (browser engine costs are real) | MOD `.github/workflows/release.yml` + NEW `.github/workflows/test.yml` |

### Sub-tasks

1. **(0.5 days)** `pip install pytest-playwright`, install Chromium,
   write the fixture that spawns + tears down a daemon.
2. **(1 day)** The walk itself. Plan to spend the bulk of time here
   on selectors — dashboard's React components don't have test IDs
   yet; either add `data-testid` attrs or use Playwright's
   accessibility-based locators.
3. **(0.5 days)** CI workflow — separate `.github/workflows/test.yml`
   that runs on every PR (not just tags). Match Phase 6's CI matrix
   shape.

### Verification

- Test passes locally: `pytest packages/core/tests/e2e/`
- Test passes in GitHub Actions on `ubuntu-latest`
- **Crucially:** intentionally reintroduce bug #77 (skip the
  hydration step), confirm the test catches it. If it doesn't, the
  test isn't doing its job; iterate until it does.

### Risks

| Risk | Mitigation |
|---|---|
| Test is slow (>1 min per run) → discourages running locally | Aim for <30s; profile if slower; use `--workers=auto` for parallelism on the unit suite (e2e stays serial) |
| Test uses real BytePlus API → cost + flakiness | Use a "mock TTS provider" registered just for the test that returns 1KB of silence — exercises the wiring without hitting real APIs |
| Browser-engine version drift breaks the test | Pin Chromium in playwright install; bump with intent |

---

## Phase 3 — Alembic migrations

**Goal:** Replace `Base.metadata.create_all()` with `alembic upgrade
head`. Existing v0.1.x installs auto-detect their orphan schema and
get stamped with the baseline migration, then upgrade. Adding a
column or table in any future PR becomes `alembic revision
--autogenerate`, not a code-archeology exercise.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 3.1 | **Alembic skeleton** | NEW `packages/core/alembic.ini` + `packages/core/alembic/` directory tree |
| 3.2 | **Baseline migration** — autogenerated from current models | NEW `packages/core/alembic/versions/0001_baseline_schema.py` |
| 3.3 | **First-run + upgrade detection** — if `alembic_version` table missing but `agents` table exists, stamp the DB with the baseline rev (treats v0.1.x DBs as already-at-baseline) | MOD `packages/core/openvox/db/__init__.py` (`init_db` rewritten to call alembic) |
| 3.4 | **`openvox migrate` CLI** — manual escape hatch for ops who want to inspect or run migrations standalone | NEW `packages/core/openvox/cli/commands/migrate.py` |
| 3.5 | **Upgrade notes** | MOD `docs/upgrade-notes.md` |

### Sub-tasks

1. **(0.5 days)** `alembic init alembic`, configure env.py to load
   settings + use the async engine.
2. **(0.5 days)** Autogenerate baseline; sanity-check the diff against
   current models (especially the cascading deletes from Phase 1).
3. **(0.5 days)** Rewrite `init_db()` to: (a) if no `alembic_version`
   table AND no other tables → fresh install, run upgrade head;
   (b) if no `alembic_version` BUT other tables exist → orphan v0.1.x
   DB, stamp with baseline rev THEN upgrade head; (c) if
   `alembic_version` exists → just upgrade head.
4. **(0.5 days)** `openvox migrate` CLI + Phase 1 tests covering the
   three init paths.

### Verification

- Fresh install (no `~/.openvox/.openvox/openvox.db`): `openvox start`
  → DB created with all current tables + `alembic_version` row.
- Upgrade from v0.1.8: rename the existing DB, run v0.1.9 daemon →
  detects orphan schema, stamps, upgrades, daemon continues working.
- Test added: write a v0.1.8-shaped DB to a temp file, point
  init_db at it, assert it upgrades cleanly.

### Risks

| Risk | Mitigation |
|---|---|
| Autogenerate misses a constraint or index | Manual diff review; add to a "migration audit" checklist in `docs/contributing.md` |
| Async SQLAlchemy + alembic has known sharp edges (env.py needs explicit `run_sync`) | Use the official async template `alembic init --template async`; standard pattern |
| Orphan-DB stamp-then-upgrade is the riskiest code path | The Phase 1 hydration test pattern applies here too — stand up a v0.1.8 DB fixture, run init, assert all data preserved |

---

## Phase 4 — Provider error message audit

**Goal:** Every "API key not set" error message mentions the wizard
as a fix path, not just `.env`. Hydration log line shows up at INFO
level in `openvox logs`. Bugs #77-78 surface as readable errors if
they ever regress.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 4.1 | **Error-message audit** — grep for `set X_API_KEY in .env`; rewrite to "set via dashboard wizard at http://localhost:8000/ OR X_API_KEY env var" | MOD ~6 files in `packages/core/openvox/api/routes/` and `packages/core/openvox/providers/` |
| 4.2 | **Hydration log visible** — fix the uvicorn-config issue where module-level INFO logs get swallowed | MOD `packages/core/openvox/cli/commands/run.py` (uvicorn log_config) |
| 4.3 | **Setup-status banner in dashboard** — if any required key is missing from BOTH env AND store, show a yellow banner at the top of every dashboard page linking to `/dashboard/setup` | MOD `apps/dashboard/src/components/nav/topbar.tsx` |

### Sub-tasks

1. **(0.5 days)** Grep + rewrite all error strings. Update the
   corresponding tests from Phase 1.
2. **(0.25 days)** Uvicorn logging config — pass `log_config=None` so
   uvicorn doesn't reset the root logger; or use a custom
   dictConfig that preserves `openvox.*` loggers at INFO.
3. **(0.25 days)** Dashboard banner.

### Verification

- Wipe `~/.openvox/`, `openvox start`, hit `/api/v1/playground/
  synthesize` → error mentions wizard URL + env var name.
- `openvox logs` after fresh start shows the "hydrated N secrets"
  line if any wizard-stored keys exist.
- Dashboard with no keys → yellow banner visible on every page.

### Risks

Minimal — pure UX text changes + a logging tweak.

---

## Phase 5 — Install matrix closure

**Goal:** All three OS install paths verified working end-to-end on
a real machine of that OS, with screenshots in `docs/install.md` as
proof. The remaining packaging bugs (Homebrew empty sha256s,
untested Windows daemon) closed.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 5.1 | **Homebrew formula sha256 fix** — switch from `poet` to `pipgrip` OR pin a poet version that handles wheel-only deps | MOD `.github/workflows/release.yml` (publish-homebrew job) |
| 5.2 | **macOS daemon smoke test** — documented re-run of the v0.1.8 smoke (already passed; capture screenshots for the docs) | MOD `docs/install.md` |
| 5.3 | **Linux daemon smoke test** — run on a real Ubuntu 24.04 VM (or systemd-in-Docker); document loginctl-linger gotcha | MOD `docs/install.md` |
| 5.4 | **Windows daemon smoke test** — Windows 11 VM, `pip install openvox-core`, verify nssm-based service install/start/stop/status all work. Document any breakage. | MOD `docs/install.md` |
| 5.5 | **`brew install` E2E pass** — after Phase 5.1 lands, cut a release, run `brew install amznsri/openvox/openvox` on a clean Mac, confirm `openvox version` works | MOD `docs/install.md` (Path C section) |

### Sub-tasks

1. **(0.5 days)** Phase 5.1 — Homebrew formula fix. Test by
   regenerating against v0.1.8 manually before next release.
2. **(0.5 days)** Phase 5.2-5.3 — Linux + macOS smokes (macOS done at
   v0.1.8, Linux on a Docker container w/ systemd).
3. **(1 day)** Phase 5.4 — Windows. This is the unknown. If the nssm
   bundling never happened, this slips to "Windows daemon: not
   supported in v0.1.x" in docs + a follow-up task spawned.
4. **(0.5 days)** Phase 5.5 — actual brew install test after the
   sha256 fix ships.

### Verification

- `brew install amznsri/openvox/openvox` on a clean macOS completes
  → `openvox version` works
- `openvox start` on Ubuntu 24.04 (`pipx install` path) succeeds →
  systemd unit enabled → `openvox status` shows running PID
- Windows: at least `pip install openvox-core && openvox run` works
  end-to-end. Daemon mode either works (great) or we document the
  gap (acceptable).

### Risks

| Risk | Mitigation |
|---|---|
| nssm.exe bundling never actually wired up | Acceptable to defer to PyInstaller follow-up; document Windows as "foreground-only via `openvox run`" for now |
| Homebrew's poet version we depend on can't be coaxed into wheel handling | Switch to `pipgrip` as planned alternative; ~+1 hour |
| User doesn't have access to a Windows VM | Use a free GitHub Actions windows-latest runner for the smoke — already cost-free since repo is public |

---

## Phase 6 — CI matrix hardening

**Goal:** Every PR runs pytest + Playwright E2E on Ubuntu + macOS (+
optionally Windows). Coverage drops fail the PR. Sub-300-second test
suite enforces a snappy feedback loop.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 6.1 | **Test workflow** | NEW `.github/workflows/test.yml` — triggers on `pull_request` + `push: branches: [main]`. Matrix: ubuntu-latest, macos-latest. Python 3.11, 3.12, 3.13. |
| 6.2 | **Coverage upload** | Codecov (free for OSS) or just inline annotation via `pytest-cov` |
| 6.3 | **Release workflow runs tests first** | MOD `.github/workflows/release.yml` — `build` job `needs: test` |
| 6.4 | **PR template + CONTRIBUTING.md** updated with the test workflow expectations | NEW `.github/PULL_REQUEST_TEMPLATE.md` + NEW `docs/contributing.md` |

### Sub-tasks

1. **(0.5 days)** Workflow YAML + matrix definition.
2. **(0.5 days)** Coverage integration + docs.

### Verification

- Open a deliberate-fail PR → CI red → can't merge
- Open a passing PR → CI green → mergeable
- Tag push v0.1.9 → release pipeline waits for tests, then publishes
- Total CI time per PR < 5 minutes

### Risks

| Risk | Mitigation |
|---|---|
| macOS minutes cost a lot more than Linux | Public repo = unlimited; we're fine. Re-evaluate if going private again. |
| Playwright on macOS runner is unreliable | Run E2E only on Ubuntu; macOS does unit tests only |

---

## Cross-cutting work

### Testing philosophy

- **Unit tests** target a single function or class, run in <100 ms
  each, no I/O beyond the test sqlite. Phase 1.
- **Integration tests** target a single subsystem with realistic
  dependencies (full sqlite, real httpx with respx mocks). Also
  Phase 1.
- **E2E tests** target the user's actual flow with a real daemon +
  real browser. Phase 2. Capped at 2-3 of these total; each one
  takes 30s+ so the unit suite stays the fast feedback loop.

### Documentation deliverables

- Phase 1: NEW `docs/contributing.md` — how to run the test suite, add
  fixtures, debug failures.
- Phase 3: MOD `docs/upgrade-notes.md` — manual migration path for
  v0.1.x → v0.1.9 (the alembic stamp-and-upgrade).
- Phase 5: MOD `docs/install.md` — per-OS daemon screenshots + the
  Linux loginctl-linger note.
- Final: MOD `README.md` — add a "Status: Beta — has test coverage,
  schema migrations, all three install paths verified" line so the
  PyPI listing looks more credible.

### Decision points (open at planning time)

1. **pytest-playwright vs pytest-asyncio interop** — should be fine
   but verify in Phase 2 sub-task 1; fall back to standalone
   Playwright if it gets messy.
2. **Coverage threshold** — propose 70% on `openvox/` modules,
   excluding `providers/` (which is mostly thin HTTP glue and not
   worth chasing). Adjustable based on what we discover.
3. **Should `openvox migrate` be exposed via the daemon HTTP API too?**
   — recommend no; ops should run it via CLI or alembic directly,
   keeps the daemon's surface area minimal.

---

## Sequencing

```
Phase 1 (tests foundation — must come first)
   │
   ├── Phase 2 (E2E — needs the conftest from 1)
   │
   ├── Phase 3 (Alembic — can overlap; benefits from Phase 1 tests)
   │
   ├── Phase 4 (error messages — can overlap; standalone)
   │
   ├── Phase 5 (install matrix — can overlap; standalone)
   │
   └── Phase 6 (CI — depends on 1, 2, 3 being done first)
```

Phases 2-5 are independent of each other and can be parallelized
across multiple sessions if multi-tracked. Phase 1 is the only
hard prerequisite for everything else.

---

## Shippable milestones

| After phase | What's possible |
|---|---|
| 1 | Any contributor can add a test; CI fails on regressions in covered modules |
| 2 | The class of bugs that broke v0.1.7 cannot regress silently |
| 3 | Adding a column to a model is `alembic revision`, not a tribal-knowledge ritual |
| 4 | Users hitting an "API key not set" error get pointed at the wizard, not just `.env` |
| 5 | All three documented install paths (pip / curl / brew) verified on real OS X 3 |
| 6 | Public claim: "v0.x.y is tested across Linux + macOS + Python 3.11-3.13" |

---

## Out of scope (deferred to later sessions)

- **PyInstaller-packaged `openvox.exe` for WinGet** (path D). Decision
  end of Session 16: defer until a real Windows non-tech user
  reports the gap. Cost is ~1-2 days + $300-400/yr code-signing cert;
  benefit is speculative pre-adoption.
- **Live interpretation pipeline** (carry-forward item #7). Theme B
  candidate for Session 18+.
- **Voice podcast generation** (carry-forward #8).
- **Speech-to-Speech via OpenAI Realtime** (carry-forward #6).
- **Cloud-hosted multi-tenant mode** (carry-forward #16). Big enough
  to deserve its own multi-session planning arc.
- **Curated MCP server catalogue** (carry-forward #3).
- **Provider-side load testing / benchmark harness**. Worth doing but
  needs a real users signal first.

---

## Handoff notes for the next session

If you (Claude, future) pick up Session 17 cold:

1. Read this doc top-to-bottom.
2. Skim `CLAUDE.md §8` for bugs #71-83 (lessons from Sessions 15-16,
   especially #82 about "phase done requires human click-through").
3. Check current branch: `git branch --show-current`. Start each
   phase on its own branch (e.g. `session17-phase1-test-suite`),
   ship as a PR, merge to main before moving to the next phase.
4. Run TESTPLAN P0 in current state to baseline before changes (the
   "before tests existed" baseline).
5. Start with Phase 1 sub-task 1 (conftest fixtures). Don't write
   any test files until the fixtures are usable — saves rework.

Open question to verify in Phase 1: **how slow does the import chain
actually get when pytest imports `openvox.api.app`?** If it's >2s per
test, we need to investigate lazy imports more aggressively. The
Phase 4 lazy-import refactor of `cli/__init__.py` helped; might need
to do the same for `api/app.py`.

If a phase's actual cost exceeds the estimate by >50%, stop and
ask the user before continuing — the plan should be re-priced
rather than blindly executed.
