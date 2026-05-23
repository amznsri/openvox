# Phase 1 dependency audit

> **Scope check before refactoring.** Inventory of every place Postgres,
> Redis, and the Node gateway are touched, so we know the actual scope of
> Phase 1 before committing to the calendar estimate in `PLANNING_SESSION15.md`.
>
> Committed on the `phase1-spike` branch; main branch unchanged.

---

## Headline finding: Phase 1 is **smaller than scoped**

The PLANNING_SESSION15.md estimate said **~2 weeks** for Phase 1. After this
audit, realistic effort is **4-5 days**. Three reasons:

1. **SQLite is already the default** — `config.py:49` defaults
   `database_url = "sqlite+aiosqlite:///./.openvox/openvox.db"`. Docker
   `docker-compose.yml` overrides via env var. Removing Postgres dependency =
   removing the env var from Docker compose. No code-level refactor needed.

2. **Redis is declared but never used.** `config.py:50` has
   `redis_url: str = "redis://localhost:6379"` — and zero other references in
   the core Python codebase. The Redis service in docker-compose.yml is dead
   weight. Removing it = delete one line in config + delete service from compose.

3. **The Node gateway is a 320-line transparent proxy.** Nothing requires
   porting. The browser/dashboard can connect directly to the Python core's
   FastAPI on port 8000, skipping the gateway entirely. The "WebSocket port"
   sub-task I scoped at 3 days isn't a port at all — it's a config change.

---

## Detailed inventory

### Postgres / database

```
packages/core/openvox/config.py:49
    database_url: str = "sqlite+aiosqlite:///./.openvox/openvox.db"   ← default already SQLite

packages/core/openvox/db/session.py:25
    _engine = create_async_engine(settings.database_url, ...)         ← respects env var
```

That's it. SQLite default works today. Postgres works today via env override.

**Phase 1.1 storage abstraction: NOT REQUIRED.** The plan called for separating
`StorageBackend` interface + `SQLiteBackend` + `PostgresBackend` implementations.
Reality: SQLAlchemy's async engine already abstracts this; both backends use the
same code path. Just verify the existing models work on SQLite for every
critical query.

**Sub-task 1.1 effort revised: ~0.5 day** (verification only, no new code).

### Redis

```
packages/core/openvox/config.py:50
    redis_url: str = "redis://localhost:6379"                          ← declared

(no other references in packages/core/openvox/*.py)                    ← never used
```

**Phase 1.2 queue abstraction: PARTIALLY REQUIRED.** The plan called for a queue
abstraction (in-process default, Redis opt-in). Reality: there's nothing
actually using Redis. APScheduler already runs in-process (verified in
Sessions 8/11). The Redis declaration is dead config.

**Sub-task 1.2 effort revised: ~0.5 day** — remove the dead Redis config line +
remove the Redis service from docker-compose.yml + verify nothing else assumed
Redis was running.

### Node gateway (packages/server/src/)

Total: **320 lines** of TypeScript across 7 files.

| File | Lines | Responsibility | After Phase 1 |
|---|---|---|---|
| `index.ts` | 86 | Fastify bootstrap | DROP — core's FastAPI does this |
| `config.ts` | 21 | Env loading | DROP — duplicates Python config |
| `routes/proxy.ts` | 44 | Transparent passthrough to core | DROP — browser → core directly |
| `routes/auth.ts` | 44 | `/me` synthetic user + OAuth scaffolds | PORT to FastAPI — 3 endpoints |
| `routes/health.ts` | 6 | `/health` | DROP — core already has `/health` |
| `routes/telephony.ts` | 37 | Was stubbed (bug #47 fix routes through proxy) | DROP — fully obsolete |
| `ws/voice.ts` | 82 | WebSocket passthrough proxy | DROP — browser → core's `/ws/voice` directly |

**Phase 1.3 "merge gateway into core" revised:**
- Auth endpoints (~30 lines of Python) — small, low-risk port
- Everything else: just delete

**Sub-task 1.3 effort revised: ~0.5 day** (port the 3 OAuth/me endpoints + delete
the rest).

### Dashboard

```
apps/dashboard/src/lib/api.ts:7
    const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001";   ← points at gateway
apps/dashboard/src/lib/api.ts:8
    const WS = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:3001";        ← points at gateway
```

In CLI mode, both should point at the core directly (`:8000`). In Docker mode,
both should continue pointing at the gateway (`:3001`).

Bug #65 (Session 14, fixed in commit `6997af7`) already verified there are no
remaining relative-URL bugs in the dashboard — all fetches route through
`api.ts`. Switching `BASE` and `WS` at build time covers everything.

**Sub-task 1.4 effort revised: ~0.5 day** — config change + verification.

### Dashboard static build

```
apps/dashboard/next.config.js (not yet inspected)
```

Need to verify `output: 'export'` works with the App Router pages we use.
Some Next.js features (server components with dynamic data, ISR, etc.) don't
work in static export. Our dashboard uses SWR for data fetching, which works
fine statically.

**Sub-task 1.5 effort: ~0.5 day** to verify + configure.

### CLI scaffold

No existing CLI in `packages/core/openvox/`. Need to add `cli/` package with
`typer` as the framework (matches the existing FastAPI/Python ecosystem).

**Sub-task 1.6 effort: ~1 day.**

---

## Revised Phase 1 schedule

| Sub-task | Original estimate | Revised | Notes |
|---|---|---|---|
| Architecture spike | 3 days | **DONE** (this doc) | Already complete |
| Storage abstraction | 1 day | 0.5 day | Just verification |
| Queue abstraction | 1 day | 0.5 day | Just remove dead config |
| WebSocket port | 3 days | 0.5 day | Browser → core directly + port `/me` |
| Dashboard static build | 1 day | 0.5 day | Config change + verification |
| CLI scaffold | 1 day | 1 day | Unchanged |
| Run full TESTPLAN both modes | 1 day | 1 day | Unchanged |
| Doc the new mode | 0.5 day | 0.5 day | Unchanged |
| **Total** | **11.5 days** | **~4.5 days** | |

**Revised calendar: ~1 week instead of 2.**

This compounds across the whole plan — Phase 2/3/4 estimates were independent of
Phase 1, so total project time becomes ~5 weeks instead of 6. Real-world
buffer still applies (+20-30% → ~6-7 weeks total).

---

## Risks discovered that were NOT in the original plan

1. **The Node gateway's auth implementation is JWT-based** (`jwt.verify` in
   `auth.ts:18`). Need to confirm: is anyone in the dashboard / SDK relying on
   this? If yes, the FastAPI port needs to issue compatible JWTs. If no
   (i.e. `OPENVOX_AUTH=enabled` is never set in practice), we can ship a simpler
   "local user only" port.

2. **`apps/dashboard` connects to the gateway for both REST and WS.** The
   environment variable plumbing exists (`NEXT_PUBLIC_API_URL`,
   `NEXT_PUBLIC_WS_URL`) but currently both point at port 3001. Dual-mode
   support means setting these at build time per mode — easy with Next.js
   build-time env vars, BUT means we'd ship two distinct dashboard builds (one
   for `:3001`/Docker, one for `:8000`/CLI). Alternative: use SAME-ORIGIN URLs
   (empty `BASE`) and let FastAPI serve everything. Worth the simplification.

3. **`packages/server` may have shared utilities used by other paths.** Audit
   `packages/server/` for anything OTHER than the 7 TS files in `src/` (e.g.,
   shared types, build scripts that other packages depend on).

---

## Decision points before sub-task 1.2 begins

Confirm with user before doing the actual refactor:

1. **Drop the Node gateway entirely** (recommended) vs. **keep it as-is for
   Docker mode and bypass for CLI mode**. The recommended path simplifies the
   codebase. The conservative path keeps Docker users on the path they know.
   Recommendation: drop entirely. The proxy adds no value once the dashboard
   can talk to core directly. Docker mode just connects the dashboard to core
   instead of to gateway.

2. **Same-origin dashboard URLs** (recommended — `BASE=""` so all fetches are
   relative to wherever the dashboard is served from) vs. **build-time env-var
   pointing at the right port**. Same-origin is simpler and matches single-
   binary architecture.

3. **PyPI name `openvox`** — reserve immediately even before Phase 1 begins.
   Free, 5 minutes. If taken, we need a different name now, not at Phase 4.

---

## Recommended next move (sub-task 1.2)

Verify SQLite parity end-to-end:

```bash
# On phase1-spike branch
unset DATABASE_URL  # forces default SQLite
cd packages/core && python -m openvox.cli run   # (cli doesn't exist yet — fall back to uvicorn)
uvicorn openvox.api.app:app --port 8000

# In another terminal — hit every critical endpoint
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/agents
curl http://localhost:8000/api/v1/templates
curl -X POST http://localhost:8000/api/v1/templates/setup-assistant/instantiate -H 'Content-Type: application/json' -d '{}'
# etc.
```

If everything works against SQLite end-to-end, we know the storage layer is
ready and Phase 1 collapses to mostly "delete Node gateway + add CLI scaffold."

If anything breaks on SQLite specifically — log the breakage here, decide
whether to fix in-place or fall back to the original Phase 1.1 (full storage
abstraction).
