#!/usr/bin/env bash
# Linux daemon smoke test for OpenVox via systemd-in-Docker.
#
# Phase 5.3 of docs/PLANNING_SESSION17.md asks for proof that
# `pipx install openvox-core && openvox start` works on Linux end-to-
# end — specifically the systemd --user path that openvox/cli/daemon/
# systemd.py installs. Real Ubuntu VM is the high-fidelity way; this
# script is the low-friction alternative: a systemd-enabled Docker
# container, a non-root user with linger enabled (the documented
# gotcha), and the same daemon-mode steps a real user would run.
#
# Usage:
#   bash scripts/smoke_linux.sh              # install latest from PyPI
#   bash scripts/smoke_linux.sh 0.2.0        # pin a specific version
#   bash scripts/smoke_linux.sh branch       # install from THIS repo's
#                                              source (the bind-mounted
#                                              packages/core directory)
#
# What it proves on success:
#   - `pipx install openvox-core` works on a stock Ubuntu 24.04.
#   - Unit file lands in ~/.config/systemd/user/openvox.service.
#   - systemctl --user enable + start register the unit + bring it up.
#   - The dashboard binds :8000 and /health returns 200.
#   - openvox status reports a live PID.
#   - openvox stop tears the unit down cleanly.
#
# What it deliberately does NOT do:
#   - Run on every PR (this is a manual one-shot — total runtime ~3 min
#     because of the apt/pip installs).
#   - Hit any real provider (no API keys, no LLM/TTS/STT calls — just
#     the install + boot + health surface).
#
# If you'd rather verify on a real Ubuntu VM than Docker, the
# equivalent host-side steps are in docs/install.md → "Path A".

set -euo pipefail

# ── Inputs ───────────────────────────────────────────────────────────
SPEC="${1:-latest}"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Build the install command for inside the container ──────────────
# Two paths: PyPI (default) for verifying a published release, or
# "branch" for verifying the in-tree source before tagging. We bind-
# mount the repo into /openvox-src so the branch path is reproducible.
case "$SPEC" in
  latest)
    INSTALL_CMD='pipx install openvox-core'
    ;;
  branch)
    INSTALL_CMD='pipx install /openvox-src/packages/core'
    ;;
  *)
    INSTALL_CMD="pipx install 'openvox-core==${SPEC}'"
    ;;
esac

echo "================================================================"
echo "OpenVox Linux daemon smoke — install spec: $SPEC"
echo "================================================================"

# ── Container guard ─────────────────────────────────────────────────
# `jrei/systemd-ubuntu` ships an Ubuntu image that boots systemd as
# PID 1 — necessary so `systemctl --user` actually has a system
# manager to talk to. The `--privileged` + cgroup mount handle the
# kernel-side requirements that vary across host kernel versions.
# (--cgroupns=host on Docker 20.10+ would work too, but --privileged
# is the most portable form across distros.)
IMAGE="jrei/systemd-ubuntu:24.04"
CONTAINER="openvox-linux-smoke"

cleanup() {
  echo
  echo "--- cleanup ---"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
echo "Starting systemd container ($IMAGE)…"
docker run -d \
  --name "$CONTAINER" \
  --privileged \
  --tmpfs /tmp \
  --tmpfs /run \
  --tmpfs /run/lock \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  -v "${REPO_ROOT}:/openvox-src:ro" \
  -p 8000:8000 \
  "$IMAGE" >/dev/null

# Give systemd a few seconds to come up — without this, `systemctl
# is-system-running` returns "starting" forever and we'd race.
echo "Waiting for systemd to finish booting…"
for _ in $(seq 1 30); do
  state=$(docker exec "$CONTAINER" systemctl is-system-running 2>/dev/null || echo starting)
  case "$state" in
    running|degraded) echo "  systemd state: $state"; break ;;
  esac
  sleep 1
done

# ── Provision a non-root user for systemd --user ─────────────────────
# The openvox daemon backend is `systemctl --user`. We need a real
# user account with linger enabled so systemd --user starts on boot
# without anyone having to log in — this is the documented gotcha
# in docs/install.md ("Linux --user services and logout").
echo
echo "--- provision non-root user ---"
docker exec "$CONTAINER" bash -c '
  set -e
  # Unprivileged user (lowest UID that avoids system-uid collision).
  # --create-home so the systemd unit + ~/.openvox land somewhere.
  # `linger` (next line) is what makes systemd --user services run
  # without a logged-in session — the documented gotcha from
  # docs/install.md "Linux --user services and logout".
  useradd -m -s /bin/bash -u 1100 openvoxer
  loginctl enable-linger openvoxer
'

# ── Install Python + pipx ────────────────────────────────────────────
echo
echo "--- apt install python3.12 + pipx ---"
docker exec "$CONTAINER" bash -c '
  set -e
  apt-get update -qq
  # python3 is 3.12 on Ubuntu 24.04. pipx ships in apt 23.04+ which
  # is what 24.04 carries, no PPA needed.
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    python3 python3-pip python3-venv pipx curl ca-certificates \
    >/dev/null
'

# ── Install + smoke-test as the non-root user ────────────────────────
# We sudo into the openvoxer user via `runuser -l`. Crucially, that
# spawns a proper login shell that picks up XDG_RUNTIME_DIR and lets
# systemctl --user reach the per-user systemd instance lingering put
# in place.
echo
echo "--- pipx install + ensurepath ---"
docker exec "$CONTAINER" runuser -l openvoxer -c "
  set -e
  ${INSTALL_CMD}
  pipx ensurepath
  echo
  echo 'openvox version:'
  ~/.local/bin/openvox version
"

echo
echo "--- openvox start (daemon, systemd --user) ---"
docker exec "$CONTAINER" runuser -l openvoxer -c '
  set -e
  ~/.local/bin/openvox start --port 8000 --host 0.0.0.0
  echo
  echo "unit file landed at:"
  ls -l ~/.config/systemd/user/openvox.service
'

echo
echo "--- wait for /health ---"
ok=0
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    body=$(docker exec "$CONTAINER" curl -fsS http://localhost:8000/health)
    echo "/health returned 200: $body"
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" -ne 1 ]]; then
  echo "::error::/health never returned 200 within 30s"
  echo "--- journalctl --user-unit=openvox.service ---"
  docker exec "$CONTAINER" runuser -l openvoxer -c 'journalctl --user-unit=openvox.service --no-pager || true'
  exit 1
fi

echo
echo "--- openvox status ---"
docker exec "$CONTAINER" runuser -l openvoxer -c '~/.local/bin/openvox status'

echo
echo "--- openvox logs (last 20 lines) ---"
docker exec "$CONTAINER" runuser -l openvoxer -c '~/.local/bin/openvox logs | tail -n 20 || true'

echo
echo "--- openvox stop ---"
docker exec "$CONTAINER" runuser -l openvoxer -c '~/.local/bin/openvox stop'

echo
echo "================================================================"
echo "✓ Linux daemon smoke passed (install spec: $SPEC)"
echo "================================================================"
