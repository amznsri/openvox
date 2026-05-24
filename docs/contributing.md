# Contributing to OpenVox

OpenVox is Apache-2.0 and accepts contributions from anyone who finds
the project useful. This page is the short version — the long-form
context lives in [`CLAUDE.md`](../CLAUDE.md) (architecture, decisions,
gotchas) and [`docs/SESSION_LOG.md`](./SESSION_LOG.md) (what we've
built and why).

## Quick start

```bash
git clone https://github.com/amznsri/openvox.git
cd openvox
python -m venv .venv && source .venv/bin/activate
pip install -e ./packages/core[dev]
pytest -m "not e2e" packages/core/tests
```

That's it for the unit suite. To run the full e2e + dashboard:

```bash
# Backend
openvox run

# Dashboard (separate terminal — dev server with hot-reload)
cd apps/dashboard && npm install && npm run dev
```

## Project structure

```
packages/core/          Python — FastAPI + voice pipeline
  openvox/api/          HTTP routes + WebSocket
  openvox/pipeline/     STT → LLM → TTS orchestrator
  openvox/providers/    Pluggable adapters (BytePlus, OpenAI, …)
  openvox/skills/       Tool implementations callable by the LLM
  openvox/cli/          `openvox <command>` entry points
  tests/                Pytest unit + e2e
apps/dashboard/         Next.js — built into a static export and
                        bundled into the wheel at release time
scripts/                Smoke tests, generators
docs/                   Install, architecture, session logs
```

Full file-by-file map in [`CLAUDE.md`](../CLAUDE.md) §4.

## How we work

- **Conventions** in [`CLAUDE.md`](../CLAUDE.md) §5. The big ones:
  Python uses type hints + `async` throughout, `from __future__ import
  annotations` on every module. Comments explain *why*, not *what*.
  Errors that a user can act on surface in the UI; ones they can't
  soft-fail with a helpful hint.
- **Don't repeat fixed bugs.** [`CLAUDE.md`](../CLAUDE.md) §8 is a
  running ledger of bugs we've already paid for. Skim it before
  touching providers, the daemon, or the install path — you'll
  often find the answer to "why does this look weird?" there.
- **Provider pattern**: each adapter implements `STTProvider` /
  `TTSProvider` / `LLMProvider` / `RTCProvider` / `VADProvider` from
  `providers/base.py`. `is_available()` checks credentials.
  Streaming methods yield events asynchronously.
- **Skills**: subclass `BaseSkill` with `id`, `display_name`,
  `description`, `parameters` (JSON schema), and an async `run()`.
  The OpenAI tool-spec is derived automatically.

## Adding tests

Tests live in `packages/core/tests/`. The pytest config in
`packages/core/pyproject.toml` defines four markers:

| Marker | Meaning |
|---|---|
| `unit` (default) | Fast (~5ms), no I/O. Default selection. |
| `e2e` | Spawns `openvox run` as a subprocess + drives over HTTP. |
| `slow` | OK to skip in normal dev. CI runs them. |
| `network` | Hits real external services. Skip in CI. |

`-m "not e2e"` is the default selector in CI's unit-tests job;
`-m e2e` selects e2e only. Use `respx` to mock HTTPX calls against
real providers — never hit the real API from a unit test.

Coverage target lives in `[tool.coverage.report]` `fail_under`; if
your PR drops below it, the unit-tests job fails. Move the bar up
in the same PR if you add new files and want the new coverage
incorporated.

## Provider keys for local testing

OpenVox is local-first; the wizard at
`http://localhost:8000/dashboard/setup` walks you through provider
keys end-to-end. For a CLI-only seed:

```bash
mkdir -p ~/.openvox
$EDITOR ~/.openvox/.env
# Set at minimum: one of BYTEPLUS_LLM_API_KEY / OPENAI_API_KEY /
# ANTHROPIC_API_KEY plus a voice key.
```

Most providers have a "test mode" or free tier. Anthropic + OpenAI
both work with $5 trial credit.

## Cutting a release (maintainers only)

1. Verify all PRs targeting the next minor / patch are merged.
2. Bump version in three places — `packages/core/pyproject.toml`,
   `packages/core/openvox/__init__.py`,
   `packages/core/openvox/api/app.py` (FastAPI `version=` arg).
3. Open a PR with just the version bumps. Merge once green.
4. From local `main`: `git tag vX.Y.Z && git push origin vX.Y.Z`.
5. `.github/workflows/release.yml` does the rest — builds the wheel,
   publishes to PyPI via Trusted Publishing, attaches install.sh +
   shas to the GitHub Release, and regenerates the Homebrew formula
   at `amznsri/homebrew-openvox`.

The release-pipeline `preflight-tests` job runs unit tests on the
tagged commit before any publish job kicks in, so a tag against
broken main self-aborts. Don't disable that gate.

## Code of Conduct

Be kind. Don't ship code that crashes someone's machine. We'll
respond to issues in good faith; please do the same.
