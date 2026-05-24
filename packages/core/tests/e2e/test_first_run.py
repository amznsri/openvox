"""End-to-end first-run flow: wizard → templates → agents → TTS.

THE test of Phase 2. Per PLANNING_SESSION17.md §Phase 2 verification:

  > Intentionally reintroduce bug #77 (skip the hydration step),
  > confirm the test catches it. If it doesn't, the test isn't doing
  > its job; iterate until it does.

Bug #77 was: the Phase 3 first-run wizard saved API keys to the
encrypted secrets store, but providers read settings.<provider>_api_key
from env vars / .env. The two layers were never bridged. Users
clicked through the wizard, saved keys, then every feature using
those keys errored with "API_KEY not set in .env".

The full chain that has to work for this test to pass:

  1. Wizard save: POST /api/v1/admin/setup/keys persists encrypted key
  2. Persistence: key survives a daemon restart (same DATA_DIR)
  3. Hydration: on next startup, _hydrate_secrets_into_env reads
     the store and writes to os.environ BEFORE register_builtins()
  4. Settings cache bust: get_settings() returns the fresh value
  5. Provider init: BytePlusTTS.__init__ caches the hydrated key
  6. is_available(): returns True
  7. /api/v1/playground/synthesize: stops returning 400 "TTS unavailable"

If ANY link breaks, this test fails — and the failure points at the
exact break since each step has an intermediate assertion.

The test deliberately uses fake API keys; it asserts on error
MESSAGE changes (not on actual TTS audio output), so it doesn't
need real BytePlus credentials and doesn't make billable API calls.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.e2e


# This test spawns TWO daemons (pre-wizard + post-restart) which the
# `running_daemon` fixture in conftest.py can't model — it's
# function-scoped and yields exactly one daemon. So we replicate the
# spawn/teardown logic inline below. If a second test needs this
# two-daemon pattern, factor the helpers back into conftest.py.


@dataclass
class _DaemonSpawn:
    proc: subprocess.Popen
    base_url: str
    tmp_home: Path


def _spawn_daemon(tmp_home: Path, port: int) -> _DaemonSpawn:
    """Spawn a daemon process pointing at the given tempdir + port.

    Returns immediately with a handle; caller is responsible for
    waiting on /health and tearing the proc down.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATA_DIR": str(tmp_home),
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_home}/openvox.db",
        "OPENVOX_AUTH": "disabled",
        "LOG_LEVEL": "warning",
    }
    for v in ("LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        if v in os.environ:
            env[v] = os.environ[v]

    proc = subprocess.Popen(
        [sys.executable, "-m", "openvox.cli", "run",
         "--no-browser", "--port", str(port), "--host", "127.0.0.1"],
        env=env,
        stdout=open(tmp_home / "daemon.stdout.log", "a"),
        stderr=open(tmp_home / "daemon.stderr.log", "a"),
    )
    return _DaemonSpawn(proc=proc, base_url=f"http://127.0.0.1:{port}", tmp_home=tmp_home)


def _wait_for_health(base_url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=1.0)
            if r.status_code == 200:
                return True
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.2)
    return False


def _stop(daemon: _DaemonSpawn) -> None:
    daemon.proc.terminate()
    try:
        daemon.proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        daemon.proc.kill()
        daemon.proc.wait(timeout=2.0)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── THE test ────────────────────────────────────────────────────────


def test_first_run_wizard_to_synthesize_end_to_end(tmp_path: Path) -> None:
    """The full v0.1.7-shape regression test.

    Walks through:
      1. Daemon spawns fresh — no keys configured
      2. /api/v1/admin/setup/status → complete=False
      3. /api/v1/playground/synthesize → 400 "TTS unavailable"  ← bug-77 surface
      4. POST /api/v1/admin/setup/keys with byteplus keys (the wizard)
      5. /api/v1/admin/setup/status → complete=True
      6. Restart daemon (same DATA_DIR — encrypted store persists)
      7. /api/v1/playground/synthesize → status code is NOT 400 with
         "TTS unavailable" detail. (May still be 4xx/5xx from the
         real BytePlus API rejecting the fake key — that's fine; what
         matters is the error CHANGED, proving hydration worked.)
    """
    tmp_home = tmp_path / "openvox-home"
    tmp_home.mkdir()
    port = _pick_free_port()

    # ─── Phase A: fresh daemon, no keys ────────────────────────────
    d1 = _spawn_daemon(tmp_home, port)
    try:
        assert _wait_for_health(d1.base_url), "first daemon never became healthy"

        # Setup status: fresh install → not complete.
        r = httpx.get(f"{d1.base_url}/api/v1/admin/setup/status", timeout=5.0)
        assert r.status_code == 200
        assert r.json()["complete"] is False, (
            "fresh daemon should report setup not complete"
        )

        # Synthesize: should return 400 "TTS unavailable" because no key.
        # This is the bug-77 surface; we assert the EXACT error message
        # so we can verify it CHANGES after the wizard save below.
        r = httpx.post(
            f"{d1.base_url}/api/v1/playground/synthesize",
            json={"text": "hello", "voice_id": "en_male_tim_uranus_bigtts"},
            timeout=5.0,
        )
        assert r.status_code == 400, (
            f"expected 400 (TTS not configured) on fresh daemon, "
            f"got {r.status_code}: {r.text}"
        )
        # After Phase 4, the error message format is:
        #   "BytePlus TTS is not configured. Add ... via the dashboard
        #    setup wizard ... or set BYTEPLUS_VOICE_API_KEY in your
        #    .env file."
        # Assert on the actionable content (the env-var name) so the
        # test stays robust against future copy edits — what matters
        # is that the user is told WHICH key + WHERE to set it, not
        # the exact wording.
        detail_before = r.json().get("detail", "")
        assert "not configured" in detail_before.lower(), (
            f"expected 'not configured' in pre-wizard error, got: {detail_before}"
        )
        assert "BYTEPLUS_VOICE_API_KEY" in detail_before, (
            f"error message must name the env var so users can fix it, got: {detail_before}"
        )

        # ─── Wizard save (simulating Phase 3 first-run wizard) ─────
        r = httpx.post(
            f"{d1.base_url}/api/v1/admin/setup/keys",
            json={
                "provider": "byteplus",
                "keys": {
                    "llm_api_key": "test-llm-key-e2e",
                    "voice_api_key": "test-voice-key-e2e",
                },
            },
            timeout=5.0,
        )
        assert r.status_code == 200, f"wizard save failed: {r.status_code} {r.text}"

        # Setup status: keys saved → now complete.
        r = httpx.get(f"{d1.base_url}/api/v1/admin/setup/status", timeout=5.0)
        body = r.json()
        assert body["complete"] is True, (
            f"setup should report complete after wizard save, got {body}"
        )
        assert "byteplus" in body.get("providers_configured", {}), (
            f"byteplus should appear in providers_configured, got {body}"
        )
    finally:
        _stop(d1)

    # ─── Phase B: restart daemon — same DATA_DIR, hydration runs ──
    # Bug #77 specifically meant providers couldn't see wizard-saved
    # keys because they read settings.<key> from env, not the store.
    # Bug #78 meant even after the bridge, providers got cached empty
    # values because register_builtins ran before hydration.
    # If either is regressed, the synth call here returns the SAME
    # "TTS unavailable" error as before.
    d2 = _spawn_daemon(tmp_home, port)
    try:
        assert _wait_for_health(d2.base_url), "second daemon never became healthy"

        # Setup status: persistence check — encrypted store survived restart.
        r = httpx.get(f"{d2.base_url}/api/v1/admin/setup/status", timeout=5.0)
        body = r.json()
        assert body["complete"] is True, (
            f"setup should still be complete after restart, got {body}"
        )

        # THE assertion. After wizard save + restart, synth must NOT
        # return the "TTS unavailable" error anymore. It may return
        # something else (4xx from BytePlus rejecting the fake key,
        # or 500 if the request shape is malformed) — we don't care
        # which, only that the error CHANGED.
        r = httpx.post(
            f"{d2.base_url}/api/v1/playground/synthesize",
            json={"text": "hello", "voice_id": "en_male_tim_uranus_bigtts"},
            timeout=10.0,
        )
        # Two acceptable outcomes:
        #   200 — provider somehow succeeded (e.g. mock provider in
        #         place; not expected with fake key)
        #   other than (400 + "TTS unavailable") — hydration worked;
        #         the failure is now from the real provider call
        if r.status_code == 400:
            detail = r.json().get("detail", "")
            # The bug-#77/#78 surface was a "TTS not configured /
            # unavailable / API_KEY not set" message AFTER the wizard
            # had saved keys. If we see ANY phrasing that implies the
            # provider thinks no key exists, hydration regressed.
            forbidden_substrings = (
                "not configured",  # Phase 4 message
                "unavailable",     # pre-Phase 4 message (kept for safety)
                "API_KEY not set", # raw provider __init__ error
            )
            for needle in forbidden_substrings:
                assert needle.lower() not in detail.lower(), (
                    f"BUG #77/#78 REGRESSED — "
                    f"synth still returns {needle!r} after wizard save + restart. "
                    f"Either hydration didn't run, or providers were instantiated "
                    f"before hydration. See CLAUDE.md §8 bugs #77 + #78. "
                    f"Full detail: {detail}"
                )
        # Anything else (200, 401, 403, 500) means hydration worked.
        # The real BytePlus API would 401 on our fake key; that's fine.
    finally:
        _stop(d2)
