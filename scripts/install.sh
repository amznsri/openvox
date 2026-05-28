#!/usr/bin/env bash
# OpenVox installer — `curl -fsSL https://openvox.ai/install.sh | bash`
#
# What this does:
#   1. Verifies a Python >= 3.11 is on PATH (or python3 / python3.11+).
#   2. Picks an install backend in order of preference:
#        a. pipx       (recommended — isolated venv, single binary on PATH)
#        b. python -m venv + pip (fallback — creates ~/.openvox/venv)
#      System pip install is intentionally NOT supported on PEP 668
#      systems (Homebrew Python, Debian/Ubuntu) because it errors out.
#   3. Installs `openvox-core` from PyPI (the package; the binary is `openvox`).
#   4. Prints next-step hints (`openvox start`, dashboard URL).
#
# Safe to re-run: idempotent. Upgrade existing installs by re-running.
#
# Override hooks (all optional):
#   OPENVOX_VERSION    Pin to a specific PyPI release (default: latest)
#   OPENVOX_INSTALLER  Force "pipx" or "venv" (default: auto)
#   OPENVOX_PREFIX     Where the venv backend installs (default: ~/.openvox/venv)
#   OPENVOX_NO_START   Set to 1 to skip the "openvox start" prompt at the end.
#
# Tested on: macOS 12+, Ubuntu 22.04+, Debian 12+, Fedora 39+.
# Source: https://github.com/amznsri/openvox

set -euo pipefail

# ── output helpers ───────────────────────────────────────────────────

# tput colours iff stdout is a tty (curl|bash typically pipes so these no-op).
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
  C_BOLD=$(tput bold)
  C_RED=$(tput setaf 1)
  C_GREEN=$(tput setaf 2)
  C_YELLOW=$(tput setaf 3)
  C_BLUE=$(tput setaf 4)
  C_RESET=$(tput sgr0)
else
  C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RESET=""
fi

info()  { printf "%s==>%s %s\n" "$C_BLUE" "$C_RESET" "$*"; }
ok()    { printf "%s ✓%s %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn()  { printf "%s ⚠%s  %s\n" "$C_YELLOW" "$C_RESET" "$*" >&2; }
fail()  { printf "%s ✗%s %s\n" "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ── platform detect (informational) ──────────────────────────────────

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)  PLATFORM="macOS" ;;
  Linux)   PLATFORM="Linux" ;;
  *)       fail "Unsupported platform: $OS. Tested on macOS + Linux. Windows: use 'pip install openvox-core' or 'winget install OpenVox.OpenVox'." ;;
esac

# ── python ≥ 3.11 ────────────────────────────────────────────────────

# Look for the right interpreter in preference order. People often have
# both python3.11 and python3 installed; prefer the newest explicit one.
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    # Verify it's actually >= 3.11; `python3` on some old distros is 3.9.
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  fail "Python 3.11+ required but not found.

Install Python first:
  macOS:   brew install python@3.12
  Ubuntu:  sudo apt install python3.12 python3.12-venv
  Fedora:  sudo dnf install python3.12

then re-run this installer."
fi

PY_VER="$("$PY" --version 2>&1)"
ok "$PY_VER on $PLATFORM/$ARCH"

# ── pick installer backend ───────────────────────────────────────────

INSTALLER="${OPENVOX_INSTALLER:-auto}"
if [ "$INSTALLER" = "auto" ]; then
  if command -v pipx >/dev/null 2>&1; then
    INSTALLER="pipx"
  else
    INSTALLER="venv"
  fi
fi

PKG_SPEC="openvox-core"
if [ -n "${OPENVOX_VERSION:-}" ]; then
  PKG_SPEC="openvox-core==$OPENVOX_VERSION"
fi

# ── install ──────────────────────────────────────────────────────────

case "$INSTALLER" in
  pipx)
    info "Installing $PKG_SPEC via pipx (isolated venv, recommended)..."
    if pipx list 2>/dev/null | grep -q "^   package openvox-core "; then
      # Already installed; upgrade in-place. `pipx upgrade` doesn't
      # honour version pins so we use install --force when pinning.
      if [ -n "${OPENVOX_VERSION:-}" ]; then
        pipx install --force "$PKG_SPEC"
      else
        pipx upgrade openvox-core || pipx install --force "$PKG_SPEC"
      fi
    else
      pipx install "$PKG_SPEC"
    fi
    OPENVOX_BIN="$(command -v openvox || echo "$HOME/.local/bin/openvox")"
    ;;
  venv)
    PREFIX="${OPENVOX_PREFIX:-$HOME/.openvox/venv}"
    info "Installing $PKG_SPEC via venv at $PREFIX..."
    if [ ! -d "$PREFIX" ]; then
      "$PY" -m venv "$PREFIX"
    fi
    # Upgrade pip + install in one shot so we don't ship a venv with a
    # crusty 2020-era pip.
    "$PREFIX/bin/pip" install --upgrade --quiet pip
    "$PREFIX/bin/pip" install --upgrade --quiet "$PKG_SPEC"
    OPENVOX_BIN="$PREFIX/bin/openvox"
    # Symlink into ~/.local/bin if it's on PATH so the user can just
    # type `openvox`. ~/.local/bin is standard per XDG and on the
    # default PATH on Fedora/Debian/Ubuntu; on macOS it's user-added
    # in many setups.
    LINK_DIR="$HOME/.local/bin"
    if [ -d "$LINK_DIR" ] || mkdir -p "$LINK_DIR" 2>/dev/null; then
      ln -sf "$OPENVOX_BIN" "$LINK_DIR/openvox"
      OPENVOX_BIN="$LINK_DIR/openvox"
    fi
    ;;
  *)
    fail "Unknown OPENVOX_INSTALLER='$INSTALLER' (expected 'pipx' or 'venv')"
    ;;
esac

ok "Installed: $("$OPENVOX_BIN" version 2>/dev/null || echo openvox)"

# ── PATH check ───────────────────────────────────────────────────────

# If the resolved binary isn't on PATH, warn — `openvox start` won't
# work cross-shell otherwise.
if ! command -v openvox >/dev/null 2>&1; then
  warn "openvox not on PATH. Add this to your shell rc and re-source:"
  case "$OPENVOX_BIN" in
    *.local/bin/*) printf "  %sexport PATH=\"\$HOME/.local/bin:\$PATH\"%s\n" "$C_BOLD" "$C_RESET" ;;
    *)             printf "  %sexport PATH=\"%s:\$PATH\"%s\n" "$C_BOLD" "$(dirname "$OPENVOX_BIN")" "$C_RESET" ;;
  esac
fi

# ── next steps ───────────────────────────────────────────────────────

cat <<EOF

${C_BOLD}> Next:${C_RESET}

  Start the background daemon:
    ${C_BOLD}openvox start${C_RESET}

  Or run in the foreground (Ctrl-C to stop):
    ${C_BOLD}openvox run${C_RESET}

  Then open: http://localhost:8000/dashboard

  See ${C_BLUE}$("$OPENVOX_BIN" --help 2>/dev/null | head -1)${C_RESET}
       or 'openvox <command> --help' for per-command flags.

EOF

# Optional: offer to start the daemon right now. Skipped if non-
# interactive (curl|bash) or if OPENVOX_NO_START=1.
if [ "${OPENVOX_NO_START:-0}" != "1" ] && [ -t 0 ]; then
  printf "Start the daemon now? [Y/n] "
  read -r answer
  case "${answer:-Y}" in
    [Nn]*) ok "Skipped. Run 'openvox start' when ready." ;;
    *)     "$OPENVOX_BIN" start ;;
  esac
fi
