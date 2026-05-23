"""`openvox info` — show resolved configuration + service status.

Useful as a first-debug command. Prints, in order:

  1. Version (mirrors `openvox version`).
  2. Resolved config — which DB / data-dir / storage / LLM provider /
     voice provider OpenVox is going to use on next `openvox run`.
     Sensitive values (API keys, JWT secret) are redacted: shown as
     `set` or `unset`, not the actual value.
  3. Service status — if the core is currently running on the
     configured port, hits `/health` and shows the response.
     Otherwise prints "not running".

Designed to be the answer to "is OpenVox actually configured right?"
for a user reporting a bug, before they need to dig through `.env`
or docker logs.
"""
from __future__ import annotations

import typer


def _redact(name: str, value: object) -> str:
    """Redact sensitive fields so `info` output is safe to paste in a bug report.

    Heuristic: any setting whose key contains 'key', 'secret', 'token',
    or 'password' is replaced with a 'set' / 'unset' tag. Everything
    else (URLs, ports, mode flags) is shown verbatim.
    """
    sensitive_markers = ("key", "secret", "token", "password")
    if any(m in name.lower() for m in sensitive_markers):
        return "set" if value else "unset"
    return repr(value)


def info_cmd() -> None:
    """Print resolved config + check whether core is reachable."""
    from openvox.config import get_settings

    settings = get_settings()

    # 1. Version line — copy of `openvox version` but inline so users
    # who run `info` first don't have to chain commands.
    try:
        from importlib.metadata import version as _pkg_version

        ver = _pkg_version("openvox-core")
    except Exception:
        ver = "0.0.0-dev"
    typer.echo(f"openvox {ver}")
    typer.echo("")

    # 2. Selected config knobs — the ones a user is likely to want to
    # verify when something's misconfigured. Not the entire settings
    # surface; that's noisy and includes 50+ provider keys.
    keys = [
        "core_port",
        "log_level",
        "openvox_auth",
        "data_dir",
        "database_url",
        "storage_backend",
        "byteplus_llm_model",
        "byteplus_llm_api_key",
        "byteplus_voice_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "openvox_insecure_tls",
    ]
    typer.echo("Configuration:")
    for k in keys:
        if hasattr(settings, k):
            val = getattr(settings, k)
            typer.echo(f"  {k:30s} = {_redact(k, val)}")
    typer.echo("")

    # 3. Liveness — is the core up on the configured port?
    typer.echo("Service status:")
    url = f"http://127.0.0.1:{settings.core_port}/health"
    try:
        # Use stdlib urllib to keep this command dependency-free
        # (we may run it before httpx is fully resolved in some
        # broken-install scenarios).
        from urllib.request import Request, urlopen

        req = Request(url, headers={"User-Agent": "openvox-cli/info"})
        with urlopen(req, timeout=2) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")[:200]
            typer.echo(f"  GET {url} → HTTP {status}")
            typer.echo(f"  body: {body}")
    except Exception as e:
        typer.echo(f"  GET {url} → not reachable ({type(e).__name__})")
        typer.echo("  Run `openvox run` to start the server.")
