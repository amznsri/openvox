"""Shared HTTP client helpers.

Two TLS pitfalls we deal with here:

1. Slim Linux images sometimes ship with a stale system CA bundle. We
   point httpx at `certifi`'s up-to-date Mozilla bundle.
2. **Corporate TLS-inspection proxies** (Zscaler, Palo Alto, Symantec
   etc.) intercept outbound HTTPS and re-sign certs with an internal
   root the container doesn't trust. There are two ways to fix this:
     a) drop the corporate root PEM at `/etc/openvox/extra-ca.pem`
        (or set `OPENVOX_EXTRA_CA_FILE`) and we'll trust it; or
     b) set `OPENVOX_INSECURE_TLS=true` to skip verification entirely
        — convenient for local development behind a trusted proxy,
        unsafe for production.
"""

from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

import certifi
import httpx

logger = logging.getLogger(__name__)

_DEFAULT_EXTRA_CA = "/etc/openvox/extra-ca.pem"


def _insecure_requested() -> bool:
    return os.environ.get("OPENVOX_INSECURE_TLS", "").lower() in ("1", "true", "yes")


def _extra_ca_path() -> str | None:
    p = os.environ.get("OPENVOX_EXTRA_CA_FILE") or _DEFAULT_EXTRA_CA
    f = Path(p)
    if not f.is_file():
        return None
    # Skip the placeholder we ship by default (comment-only file).
    try:
        text = f.read_text(errors="ignore")
    except OSError:
        return None
    if "BEGIN CERTIFICATE" not in text:
        return None
    return p


def certifi_ssl_context() -> ssl.SSLContext | None:
    """Build an SSL context. Returns None when verification is disabled."""
    if _insecure_requested():
        logger.warning(
            "OPENVOX_INSECURE_TLS=true — outbound TLS verification is OFF. "
            "Use only on trusted networks (e.g. behind a corporate proxy)."
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ctx = ssl.create_default_context(cafile=certifi.where())
    extra = _extra_ca_path()
    if extra:
        try:
            ctx.load_verify_locations(cafile=extra)
            logger.info("loaded extra CA bundle: %s", extra)
        except Exception as e:
            logger.warning("failed to load extra CA at %s: %s", extra, e)
    return ctx


def make_async_client(
    *,
    timeout: float = 60.0,
    connect_timeout: float = 5.0,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Create an `httpx.AsyncClient` honouring our TLS conventions."""
    if _insecure_requested():
        verify: bool | ssl.SSLContext = False
    else:
        verify = certifi_ssl_context() or False
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        headers=headers or {},
        verify=verify,
    )
