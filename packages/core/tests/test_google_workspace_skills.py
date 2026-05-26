"""Native Gmail + Calendar skills (Phase 1.4).

Each test mocks Google's HTTP endpoints via respx and asserts:
  - Bearer token comes from the token store (round-trip via Phase 1.3),
  - request shape matches Google's API contract,
  - parsed result has the shape downstream agent prompts expect.

A handful also exercise the multi-account selection logic — that
matters because the LLM should be able to call these skills without
knowing which Google account to use when only one is connected, but
must be told which when multiple are connected.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import httpx
import pytest


# ── Fixture helpers ───────────────────────────────────────────────


async def _connect_account(email: str, expires_in_seconds: int = 3600) -> None:
    """Helper: write a Google integration into the token store."""
    from openvox.oauth import set_oauth_token

    await set_oauth_token(
        provider="google",
        user_email=email,
        access_token=f"atok-{email}",
        refresh_token=f"rtok-{email}",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        scopes=["openid", "email", "https://www.googleapis.com/auth/gmail.modify"],
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
    )


def _ctx():
    from openvox.skills.base import SkillContext

    return SkillContext(session_id="s", agent_id="a", user_id="u")


# ── User-resolution behaviour ─────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_errors_when_no_google_account_connected(isolated_db):
    """Skills surface an actionable error pointing at the dashboard."""
    from openvox.skills.builtin.google_workspace import ListEmails

    skill = ListEmails()
    with pytest.raises(ValueError, match="No Google account is connected"):
        await skill.run({}, _ctx())


@pytest.mark.asyncio
async def test_skill_errors_when_multiple_accounts_and_none_specified(
    isolated_db,
):
    """LLM must disambiguate when more than one account is connected."""
    await _connect_account("personal@example.com")
    await _connect_account("work@example.com")

    from openvox.skills.builtin.google_workspace import ListEmails

    skill = ListEmails()
    with pytest.raises(ValueError, match="Multiple Google accounts"):
        await skill.run({}, _ctx())


@pytest.mark.asyncio
async def test_skill_auto_selects_when_one_account(isolated_db):
    """Single connected account → no user_email needed."""
    import respx

    await _connect_account("only@example.com")

    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        ).mock(return_value=httpx.Response(200, json={"messages": []}))

        from openvox.skills.builtin.google_workspace import ListEmails

        result = await ListEmails().run({}, _ctx())

    assert result["account"] == "only@example.com"
    assert result["count"] == 0


# ── ListEmails ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_emails_returns_parsed_metadata(isolated_db):
    """Listing + per-message metadata get composed into a flat list."""
    import respx

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=False) as router:
        router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        ).mock(
            return_value=httpx.Response(
                200, json={"messages": [{"id": "m1"}, {"id": "m2"}]}
            )
        )
        router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "hello there",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Lunch?"},
                            {"name": "From", "value": "bob@example.com"},
                            {"name": "To", "value": "alice@example.com"},
                            {"name": "Date", "value": "Mon, 26 May 2026 09:00:00 +0000"},
                        ]
                    },
                },
            )
        )
        router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/m2"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "m2",
                    "threadId": "t2",
                    "snippet": "FYI",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Re: Invoice"},
                            {"name": "From", "value": "billing@vendor.com"},
                        ]
                    },
                },
            )
        )

        from openvox.skills.builtin.google_workspace import ListEmails

        result = await ListEmails().run(
            {"query": "is:unread", "max_results": 5}, _ctx()
        )

    assert result["count"] == 2
    assert result["query"] == "is:unread"
    assert result["messages"][0]["subject"] == "Lunch?"
    assert result["messages"][0]["from"] == "bob@example.com"
    assert result["messages"][0]["is_unread"] is True
    assert result["messages"][1]["subject"] == "Re: Invoice"
    assert result["messages"][1]["is_unread"] is False


@pytest.mark.asyncio
async def test_list_emails_caps_max_results(isolated_db):
    """maxResults query param is clamped to [1, 50] regardless of LLM."""
    import respx

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        route = router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        ).mock(return_value=httpx.Response(200, json={"messages": []}))

        from openvox.skills.builtin.google_workspace import ListEmails

        await ListEmails().run({"max_results": 999}, _ctx())

    last = route.calls.last.request
    # respx exposes query as a string — parse it.
    import urllib.parse

    qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(str(last.url)).query))
    assert qs["maxResults"] == "50"


# ── ReadEmail ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_email_decodes_plaintext_body(isolated_db):
    """text/plain part is base64url-decoded to a string."""
    import respx

    await _connect_account("alice@example.com")

    body = "Hello Alice,\n\nLunch at 1pm?\n\n— Bob"
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")

    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/abc"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "abc",
                    "threadId": "t",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Lunch"},
                            {"name": "From", "value": "bob@example.com"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": encoded},
                    },
                },
            )
        )

        from openvox.skills.builtin.google_workspace import ReadEmail

        result = await ReadEmail().run({"message_id": "abc"}, _ctx())

    assert "Hello Alice" in result["body"]
    assert "Lunch at 1pm?" in result["body"]
    assert result["subject"] == "Lunch"


@pytest.mark.asyncio
async def test_read_email_walks_multipart(isolated_db):
    """Nested multipart trees: walk until we find text/plain."""
    import respx

    await _connect_account("alice@example.com")

    inner = base64.urlsafe_b64encode(b"plain body here").decode().rstrip("=")
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/m"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "m",
                    "payload": {
                        "mimeType": "multipart/alternative",
                        "headers": [],
                        "parts": [
                            {"mimeType": "text/html", "body": {"data": "ZmFsbGJhY2s"}},
                            {"mimeType": "text/plain", "body": {"data": inner}},
                        ],
                    },
                },
            )
        )

        from openvox.skills.builtin.google_workspace import ReadEmail

        result = await ReadEmail().run({"message_id": "m"}, _ctx())

    # text/plain wins over text/html when both present.
    assert result["body"] == "plain body here"


# ── SendEmail ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_email_posts_rfc5322(isolated_db):
    """Body is built as RFC 5322 + base64url-encoded for the API."""
    import respx

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        route = router.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        ).mock(
            return_value=httpx.Response(
                200, json={"id": "sent-1", "threadId": "t-sent"}
            )
        )

        from openvox.skills.builtin.google_workspace import SendEmail

        result = await SendEmail().run(
            {
                "to": "bob@example.com",
                "subject": "Lunch?",
                "body": "Are you free at 1pm?",
            },
            _ctx(),
        )

    body = route.calls.last.request.read()
    import json

    parsed = json.loads(body)
    raw = base64.urlsafe_b64decode(parsed["raw"] + "==").decode()
    # The RFC 5322 envelope must mention the to/subject. The body
    # is base64-encoded inside the MIME part (MIMEText default for
    # utf-8 charset) — assert on the decoded payload separately.
    assert "To: bob@example.com" in raw
    assert "Subject: Lunch?" in raw
    body_b64 = raw.split("\n\n", 1)[1].strip()
    assert base64.b64decode(body_b64).decode() == "Are you free at 1pm?"
    assert result["id"] == "sent-1"


@pytest.mark.asyncio
async def test_send_email_validates_required_fields(isolated_db):
    from openvox.skills.builtin.google_workspace import SendEmail

    await _connect_account("alice@example.com")
    skill = SendEmail()
    with pytest.raises(ValueError, match="to"):
        await skill.run({"subject": "x", "body": "y"}, _ctx())
    with pytest.raises(ValueError, match="subject"):
        await skill.run({"to": "x@y", "body": "y"}, _ctx())
    with pytest.raises(ValueError, match="body"):
        await skill.run({"to": "x@y", "subject": "s"}, _ctx())


# ── Calendar ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_calendar_events_returns_flat_list(isolated_db):
    import respx

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Standup",
                            "start": {"dateTime": "2026-05-27T09:00:00Z"},
                            "end": {"dateTime": "2026-05-27T09:30:00Z"},
                            "attendees": [
                                {"email": "bob@example.com", "responseStatus": "accepted"}
                            ],
                            "htmlLink": "https://calendar.google.com/...",
                        }
                    ]
                },
            )
        )

        from openvox.skills.builtin.google_workspace import ListCalendarEvents

        result = await ListCalendarEvents().run({}, _ctx())

    assert result["count"] == 1
    ev = result["events"][0]
    assert ev["summary"] == "Standup"
    assert ev["start"] == "2026-05-27T09:00:00Z"
    assert ev["attendees"][0]["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_create_calendar_event_posts_normalised_body(isolated_db):
    """start/end strings are normalised to Calendar's structured shape."""
    import respx

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        route = router.post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "new-1",
                    "summary": "Coffee w/ Bob",
                    "start": {"dateTime": "2026-05-27T10:00:00Z"},
                    "end": {"dateTime": "2026-05-27T10:30:00Z"},
                    "htmlLink": "https://calendar.google.com/...",
                },
            )
        )

        from openvox.skills.builtin.google_workspace import CreateCalendarEvent

        result = await CreateCalendarEvent().run(
            {
                "summary": "Coffee w/ Bob",
                "start": "2026-05-27T10:00:00Z",
                "end": "2026-05-27T10:30:00Z",
                "attendees": ["bob@example.com"],
            },
            _ctx(),
        )

    import json

    body = json.loads(route.calls.last.request.read())
    assert body["summary"] == "Coffee w/ Bob"
    assert body["start"]["dateTime"] == "2026-05-27T10:00:00Z"
    assert body["end"]["dateTime"] == "2026-05-27T10:30:00Z"
    assert body["attendees"] == [{"email": "bob@example.com"}]
    assert result["id"] == "new-1"


@pytest.mark.asyncio
async def test_delete_calendar_event(isolated_db):
    """DELETE returns 204 + no body; skill handles that gracefully."""
    import respx

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        router.delete(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events/del-1"
        ).mock(return_value=httpx.Response(204))

        from openvox.skills.builtin.google_workspace import DeleteCalendarEvent

        result = await DeleteCalendarEvent().run({"event_id": "del-1"}, _ctx())

    assert result["deleted"] is True
    assert result["id"] == "del-1"


@pytest.mark.asyncio
async def test_find_free_time_skips_busy_intervals(isolated_db):
    """Free-time finder respects existing events."""
    import respx

    await _connect_account("alice@example.com")

    # Mock returns a single 1-hour event blocking 10:00-11:00 UTC.
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "start": {"dateTime": "2026-05-27T10:00:00+00:00"},
                            "end": {"dateTime": "2026-05-27T11:00:00+00:00"},
                        }
                    ]
                },
            )
        )

        from openvox.skills.builtin.google_workspace import FindFreeTime

        result = await FindFreeTime().run(
            {
                "duration_min": 30,
                "time_min": "2026-05-27T09:00:00+00:00",
                "time_max": "2026-05-27T13:00:00+00:00",
                "max_results": 10,
                "working_hours_start": 9,
                "working_hours_end": 18,
            },
            _ctx(),
        )

    starts = [s["start"] for s in result["slots"]]
    # 9:00 should be available, 10:00 / 10:30 should NOT.
    assert any(s.startswith("2026-05-27T09:00") for s in starts)
    assert not any(s.startswith("2026-05-27T10:00") for s in starts)
    assert not any(s.startswith("2026-05-27T10:30") for s in starts)


# ── Token refresh path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_token_triggers_refresh(isolated_db, monkeypatch):
    """An expired bundle is silently refreshed before the API call.

    Critical guarantee: the skill layer NEVER calls Google with a
    stale access_token. This test seeds a bundle whose expires_at is
    in the past, mocks the refresh response, and asserts the refresh
    happened + the refreshed token is what was used on the API call.
    """
    import respx

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    # Connect with a token that expired 5 minutes ago.
    await _connect_account("alice@example.com", expires_in_seconds=-300)

    with respx.mock(assert_all_called=True) as router:
        refresh_route = router.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ya29.fresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        )
        gmail_route = router.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        ).mock(return_value=httpx.Response(200, json={"messages": []}))

        from openvox.skills.builtin.google_workspace import ListEmails

        await ListEmails().run({}, _ctx())

    assert refresh_route.called
    # The Bearer header on the Gmail call carries the REFRESHED token.
    last_call_headers = gmail_route.calls.last.request.headers
    assert last_call_headers["authorization"] == "Bearer ya29.fresh"

    # And the store row was updated.
    from openvox.oauth import get_oauth_token

    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle.access_token == "ya29.fresh"
    assert bundle.is_expired is False
