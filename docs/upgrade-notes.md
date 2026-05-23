# Upgrade notes

Breaking-change checklist for operators upgrading an existing OpenVox install.
Newest first.

## After Phase 1 PR-1 (commit `f66438a`) — Node gateway + Redis deleted

If you pulled the Phase 1 PR-1 commit on an existing install, two old
containers will still be running because Docker Compose can't manage
services that no longer exist in the file. One-time manual cleanup:

```bash
docker stop openvox-server openvox-redis
docker rm   openvox-server openvox-redis
docker volume rm openvox_redis-data   # optional — wasn't being used anyway
docker compose up --build
```

After this, `docker compose ps` should show **4 services** (down from 6):

| Service | Status |
|---|---|
| `openvox-core` | Up + healthy |
| `openvox-dashboard` | Up |
| `openvox-postgres` | Up + healthy |
| `openvox-whatsapp-bridge` *(opt-in)* | Up if `--profile whatsapp` was used |

The dashboard now connects DIRECTLY to the FastAPI core at port 8000
(was via the Node gateway on 3001 previously). `NEXT_PUBLIC_API_URL`
and `NEXT_PUBLIC_WS_URL` in `docker-compose.yml` were updated
accordingly; if you have a custom `.env` override pointing at port
3001, switch it to 8000.

### Why the deletion was safe

The Phase 1 audit (`docs/phase1-audit.md`) established that the Node
gateway was 320 LoC of pure passthrough proxy — the dashboard had no
features that relied on gateway transformation. The `/me` and OAuth-
start scaffolds it hosted were ported to `packages/core/openvox/api/
routes/auth.py`.

Redis was declared in `config.py:50` but **never imported anywhere**
in `packages/core/openvox/**/*.py`. APScheduler had been running
in-process the whole time.

## Earlier upgrades

See `SESSION_LOG.md` for the per-session history. Notable earlier
breakages worth re-checking after a long-paused install:

- Bug #41 (Session 9): if you do `docker cp packages/core/openvox/`
  watch the trailing `/.` — without it you end up with
  `/app/openvox/openvox/` (nested).
- Bug #53 (Session 11): if your install predates the FK-cascade
  fixes, deleting an agent could fail with FK-violation errors.
  Solution: pull current main + re-create any broken records.
