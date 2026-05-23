# Install — CLI mode

OpenVox ships two install modes. This page covers **CLI mode** — the
lightweight, single-process install for personal / development use. For
the heavier Docker compose stack with Postgres + per-service isolation
(production / multi-tenant), see [`install-docker.md`](./install-docker.md).

> **For end-users**: prefer the four packaged install paths in
> [`docs/install.md`](./install.md) (pip / curl-bash / brew / winget).
> This page covers the source-checkout / contributor flow + the
> internals of how daemon mode works on each OS.
>
> **Status (Session 16):** daemon mode (`openvox start / stop / status /
> restart / logs`) shipped in Phase 4 PR-1 with per-OS backends
> (launchd / systemd --user / Windows Service via nssm). PyPI +
> Homebrew + WinGet packaging metadata shipped in PR-2/PR-4. The
> release pipeline that publishes to all four channels on tag-push
> shipped in PR-5; a one-time PyPI Trusted Publisher / tap-repo /
> winget-fork setup gates the first real release.

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

## Daemon mode (Phase 4 PR-1, shipped)

Always-running, survives terminal close, auto-starts at user login:

```bash
openvox start          # install (if needed) + start the daemon
openvox stop
openvox status         # running / stopped / unknown + PID
openvox restart
openvox logs -f        # tail ~/.openvox/logs/openvox.log
```

Backed by the per-OS native service manager. Implementation lives in
`packages/core/openvox/cli/daemon/`:

| OS | Backend | Service file | Stops on logout? |
|---|---|---|---|
| macOS | `LaunchdBackend` (launchctl) | `~/Library/LaunchAgents/com.openvox.daemon.plist` | No (LaunchAgent persists across sessions). |
| Linux | `SystemdBackend` (systemctl --user) | `~/.config/systemd/user/openvox.service` | Yes by default. `loginctl enable-linger $USER` to keep running. |
| Windows | `WindowsServiceBackend` (nssm.exe) | Service: `OpenVoxDaemon` | No (runs as a true Windows Service). |

`openvox onboard` (terminal-only first-run wizard) is the remaining
Phase 4 follow-up — the dashboard wizard from Phase 3 covers non-
headless onboarding today.

## Four install paths (no source checkout)

```bash
pip install openvox-core                       # path A: Python users
curl -fsSL https://github.com/amznsri/openvox/releases/latest/download/install.sh | bash   # path B: macOS / Linux
brew install amznsri/openvox/openvox           # path C: macOS / Linux via Homebrew
winget install OpenVox.OpenVox                 # path D: Windows
```

Detail per path in [`docs/install.md`](./install.md). The release
pipeline that builds + publishes to all four channels on tag push
lives at `.github/workflows/release.yml`.

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
