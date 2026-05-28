"""Telegram Bot API wrapper — verify / webhook / send / download.

Telegram is the simplest of our messaging channels:
    - JSON everywhere (no XML, no AES).
    - Bots created manually via @BotFather in the Telegram app itself.
    - Each bot identified by `bot_token` of shape "123456:ABC-DEF..."
    - Webhook receives Updates as POST JSON; we verify auth via a
      `secret_token` header that we choose when we call `setWebhook`.

This file is a thin functional wrapper. The webhook *handler* (which
parses Updates, downloads voice files, runs the VoiceSession, posts
replies) lives in `api/routes/telephony.py` so it shares logging /
DB sessions with the rest of the HTTP surface.

Why the secret_token matters:
    Without it, anyone who knows your webhook URL can POST arbitrary
    Updates and the bot will act on them. With it, Telegram includes
    `X-Telegram-Bot-Api-Secret-Token: <ours>` on every legitimate
    POST. We reject anything without the right header → public URL
    leaks become harmless.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from openvox.utils.http import make_async_client

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_FILE_BASE = "https://api.telegram.org/file/bot{token}/{path}"


def _url(token: str, method: str) -> str:
    return _API_BASE.format(token=token, method=method)


def generate_webhook_secret() -> str:
    """Random URL-safe token for the `X-Telegram-Bot-Api-Secret-Token`
    header. 32 chars of entropy, fits the Telegram constraint
    (1..256 chars of `[A-Za-z0-9_-]`)."""
    return secrets.token_urlsafe(24)


# ── Verification ────────────────────────────────────────────────────


async def verify_bot(token: str) -> dict[str, Any]:
    """Confirm the token is valid by calling `getMe`.

    Returns the bot User object: {id, is_bot, first_name, username, ...}
    Raises RuntimeError with the API's `description` field on failure
    so the dashboard can surface "401 Unauthorized" verbatim to the user.
    """
    if not token or ":" not in token:
        raise RuntimeError("bot token looks malformed (missing ':')")
    async with make_async_client(timeout=10.0) as c:
        r = await c.get(_url(token, "getMe"))
    data = r.json() if r.content else {}
    if r.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"telegram getMe failed: status={r.status_code} "
            f"description={data.get('description') or r.text[:200]}"
        )
    return data["result"]


# ── Webhook registration ────────────────────────────────────────────


async def set_webhook(
    token: str,
    *,
    url: str,
    secret_token: str,
    allowed_updates: list[str] | None = None,
    drop_pending_updates: bool = True,
) -> dict[str, Any]:
    """Point Telegram at our webhook URL.

    `allowed_updates` filters which Update kinds Telegram will deliver.
    We default to just `message` since callback_query / inline_query
    aren't wired up yet — picking them up later is purely additive.
    """
    body = {
        "url": url,
        "secret_token": secret_token,
        "drop_pending_updates": drop_pending_updates,
        "allowed_updates": allowed_updates or ["message"],
    }
    async with make_async_client(timeout=10.0) as c:
        r = await c.post(_url(token, "setWebhook"), json=body)
    data = r.json() if r.content else {}
    if r.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"setWebhook failed: status={r.status_code} "
            f"description={data.get('description') or r.text[:200]}"
        )
    return data["result"] if isinstance(data.get("result"), dict) else {"ok": True}


async def delete_webhook(token: str) -> None:
    """Unsubscribe from Telegram updates. Used on Disconnect."""
    async with make_async_client(timeout=10.0) as c:
        await c.post(_url(token, "deleteWebhook"), json={"drop_pending_updates": True})


# ── File download (voice messages) ──────────────────────────────────


async def download_file(token: str, file_id: str) -> tuple[bytes, str]:
    """Two-hop download: getFile → CDN URL → bytes.

    Returns `(bytes, mime_hint)`. Telegram voice messages are always
    OGG-Opus (`audio/ogg`); other audio messages can be mp3/m4a/wav
    — we pass the file extension as the hint so pydub picks the
    right ffmpeg codec.
    """
    async with make_async_client(timeout=30.0) as c:
        r1 = await c.get(_url(token, "getFile"), params={"file_id": file_id})
        meta = r1.json() if r1.content else {}
        if r1.status_code != 200 or not meta.get("ok"):
            raise RuntimeError(f"getFile failed: {meta.get('description') or r1.text[:200]}")
        file_path = (meta.get("result") or {}).get("file_path") or ""
        if not file_path:
            raise RuntimeError("getFile returned no file_path")
        r2 = await c.get(_FILE_BASE.format(token=token, path=file_path))
        if r2.status_code != 200:
            raise RuntimeError(f"file download failed: status={r2.status_code}")
        ext = (file_path.rsplit(".", 1)[-1] or "ogg").lower()
        return r2.content, ext


# ── Sending ─────────────────────────────────────────────────────────


async def send_text(token: str, chat_id: int | str, text: str) -> None:
    """Send a text message. We don't yet do Markdown / HTML parsing —
    plain text avoids "unexpected entity" errors on free-form LLM
    output. If you want fancy formatting, wrap this with
    `parse_mode="HTML"` and escape `< > &`."""
    if not text:
        return
    # Telegram caps message text at 4096 chars. Split rather than fail —
    # a long assistant reply still lands, just in two bubbles.
    for chunk in _chunks(text, 4096):
        async with make_async_client(timeout=10.0) as c:
            r = await c.post(
                _url(token, "sendMessage"),
                json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            )
        if r.status_code != 200:
            logger.warning("sendMessage failed: %s %s", r.status_code, r.text[:200])


async def send_voice(token: str, chat_id: int | str, ogg_bytes: bytes) -> None:
    """Upload an OGG-Opus voice note (the same format users send to bots).

    Caller is responsible for encoding PCM → OGG-Opus first. We use
    `sendVoice` (not `sendAudio`) so it renders as a voice note bubble
    with a waveform, matching the chat UX of human voice messages.
    """
    if not ogg_bytes:
        return
    async with make_async_client(timeout=30.0) as c:
        files = {"voice": ("reply.ogg", ogg_bytes, "audio/ogg")}
        data = {"chat_id": str(chat_id)}
        r = await c.post(_url(token, "sendVoice"), data=data, files=files)
    if r.status_code != 200:
        logger.warning("sendVoice failed: %s %s", r.status_code, r.text[:200])


async def send_chat_action(token: str, chat_id: int | str, action: str = "typing") -> None:
    """Show a "typing…" or "record audio…" indicator while the agent
    works. Actions: typing | upload_voice | record_voice | record_video.
    Best-effort — failure here doesn't break the conversation."""
    try:
        async with make_async_client(timeout=5.0) as c:
            await c.post(
                _url(token, "sendChatAction"),
                json={"chat_id": chat_id, "action": action},
            )
    except Exception:
        pass


# ── Helpers ─────────────────────────────────────────────────────────


def _chunks(text: str, max_len: int):
    """Split on paragraph boundaries when possible, else hard-cut."""
    if len(text) <= max_len:
        yield text
        return
    cur = ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > max_len:
            if cur:
                yield cur
            cur = para
        else:
            cur = (cur + "\n\n" + para) if cur else para
    if cur:
        # If a single paragraph still exceeds the limit (rare), hard-cut.
        while len(cur) > max_len:
            yield cur[:max_len]
            cur = cur[max_len:]
        if cur:
            yield cur
