# Install — CLI mode

OpenVox ships two install modes. This page covers **CLI mode** — the
lightweight, single-process install for personal / development use. For
the heavier Docker compose stack with Postgres + per-service isolation
(production / multi-tenant), see [`install-docker.md`](./install-docker.md).

> **Status (Session 15):** the CLI is functional today via the source-
> checkout path below. Public PyPI distribution (`pip install openvox-core`)
> + native installers (Homebrew formula, curl-bash, WinGet) ship in
> Phase 4 per [`PLANNING_SESSION15.md`](./PLANNING_SESSION15.md). The
> dashboard is currently served by a separate Next.js process — single-
> process serving via FastAPI static-export needs a small dashboard
> refactor (the `agents/[id]` route → query params) that's queued for
> a follow-up PR.

## Quick install (source checkout, today)

```bash
# 1. Get the source
git clone https://github.com/amznsri/openvox.git
cd openvox/packages/core

# 2. Install (editable, picks up any local edits without re-install)
pip install -e .

# 3. (Optional) Configure
cp ../../.env.example ../../.env
$EDITOR ../../.env      # paste BYTEPLUS_LLM_API_KEY or OPENAI_API_KEY at minimum

# 4. Run
openvox run
```

The `openvox run` command starts the FastAPI server on port 8000 (or
`$CORE_PORT`) and opens your browser to the dashboard. Ctrl-C to stop.

## What the CLI gives you (Phase 1 PR-2)

```text
$ openvox --help
                                                                                
 Usage: openvox [OPTIONS] COMMAND [ARGS]...                                     
                                                                                
 OpenVox — the open-source platform for building production voice agents.       
                                                                                
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ version  Print the installed OpenVox version.                                │
│ info     Show resolved configuration + service health.                       │
│ run      Start the FastAPI server in the foreground and open the dashboard.  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

| Command | What it does |
|---|---|
| `openvox version` | Prints the installed version. |
| `openvox info` | Resolved config (API keys redacted as `set`/`unset`) + health check against the running service. Great first-debug command for "is OpenVox configured right?" |
| `openvox run` | Foreground server + auto-opens browser. Press Ctrl-C to stop. |
| `openvox run --port 9000` | Custom port (default 8000). |
| `openvox run --no-browser` | Skip the auto-browser-open (headless / SSH). |
| `openvox run --host 127.0.0.1` | Local-only binding (default `0.0.0.0` allows LAN access). |

## Coming in Phase 4

Daemon mode (always-running, survives terminal close, auto-starts at boot):

```bash
openvox start          # background service (launchd/systemd/Windows Service)
openvox stop
openvox status
openvox restart
openvox logs           # tail the service log
openvox onboard        # first-run interactive setup (API keys, template, channel)
```

Plus four free install paths (no source checkout needed):

```bash
pip install openvox-core           # path A: Python users
curl -fsSL openvox.ai/install.sh | bash   # path B: one-line anyone
brew install openvox               # path C: macOS Homebrew
winget install OpenVox.OpenVox     # path D: Windows
```

## CLI mode vs Docker mode — when to use which

| Use Docker mode when… | Use CLI mode when… |
|---|---|
| You need Postgres (multi-tenant, large session counts) | Personal use; one operator |
| You're running production with separate services for isolation | Local development / contribution |
| You want the dashboard's Next.js dev hot-reload during UI work | You don't need a dashboard hot-reload loop |
| You're behind corporate proxies / locked-down Linux servers (Docker isolates everything) | You're on your own laptop / dev VM |

Both modes share the **same database**, **same agents**, **same skills**,
**same templates**, **same SDK**. You can migrate between modes by
swapping the `DATABASE_URL` env var (SQLite in CLI mode by default;
Postgres in Docker mode).
