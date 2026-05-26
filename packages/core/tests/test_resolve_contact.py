"""Tests for the Phase 2 ``resolve_contact`` skill (People API).

Coverage:
  - Happy path: searchContacts returns one match → flat dict back
  - Multiple matches: server-sorted order preserved + organizations
    surfaced so the LLM can disambiguate.
  - Empty initial response → warmup-then-retry path fires once,
    second call's results are returned.
  - 403 PERMISSION_DENIED → readable hint pointing at Integrations
    tab (the common case: Phase 1 user hasn't reconnected to grant
    the new contacts.readonly scope).
  - Bearer header carries the freshly-refreshed access_token.
  - Skill is registered in `SKILLS` and exposes a sensible tool spec.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest


# ── Fixture helpers ──────────────────────────────────────────────


async def _connect_account(email: str):
    """Insert a fresh, non-expired Google integration into the token store."""
    from openvox.oauth import set_oauth_token

    await set_oauth_token(
        provider="google",
        user_email=email,
        access_token=f"atok-{email}",
        refresh_token=f"rtok-{email}",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        scopes=[
            "openid", "email",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/contacts.readonly",
        ],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _ctx():
    from openvox.skills.base import SkillContext
    return SkillContext(session_id="s", agent_id="a", user_id="u")


PEOPLE_SEARCH_URL = "https://people.googleapis.com/v1/people:searchContacts"


# ── Registration / spec ──────────────────────────────────────────


def test_resolve_contact_is_registered():
    """Phase 2 lands the new skill — make sure SKILLS exposes it."""
    from openvox.skills.builtin.google_workspace import SKILLS

    ids = [cls.id for cls in SKILLS]
    assert "resolve_contact" in ids


def test_resolve_contact_tool_spec_shape():
    """LLM-facing description + parameters look right.

    We check the basics: required field is `query`, optional
    `max_results` and `user_email` are advertised, description
    mentions People API (the LLM's signal for "use this BEFORE
    search_contacts_in_gmail").
    """
    from openvox.skills.builtin.google_workspace import ResolveContact

    spec = ResolveContact().to_tool_spec()
    fn = spec["function"]
    assert fn["name"] == "resolve_contact"
    assert "People API" in fn["description"]
    params = fn["parameters"]
    assert params["required"] == ["query"]
    assert "max_results" in params["properties"]
    assert "user_email" in params["properties"]


# ── Happy paths ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_contact_single_match(isolated_db):
    """One contact in Google Contacts → flat email + name back."""
    import respx
    from openvox.skills.builtin.google_workspace import ResolveContact

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        route = router.get(PEOPLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "person": {
                                "names": [{"displayName": "John Doe"}],
                                "emailAddresses": [
                                    {"value": "john.doe@acme.com", "type": "work"}
                                ],
                                "photos": [{"url": "https://lh.example/photo.jpg"}],
                                "organizations": [
                                    {"name": "Acme Inc", "title": "VP Sales"}
                                ],
                            }
                        }
                    ]
                },
            )
        )

        result = await ResolveContact().run({"query": "John Doe"}, _ctx())

    assert result["count"] == 1
    contact = result["contacts"][0]
    assert contact["name"] == "John Doe"
    assert contact["emails"][0]["address"] == "john.doe@acme.com"
    assert contact["emails"][0]["type"] == "work"
    assert contact["photo_url"] == "https://lh.example/photo.jpg"
    assert contact["organizations"] == [
        {"name": "Acme Inc", "title": "VP Sales"}
    ]

    # Right endpoint hit + Bearer carries the integration's token.
    assert route.called
    last = route.calls.last.request
    assert "Bearer atok-alice@example.com" == last.headers["authorization"]


@pytest.mark.asyncio
async def test_resolve_contact_multiple_matches_preserves_order(isolated_db):
    """Two Johns → both returned in server-sorted order with orgs.

    The Exec Assistant prompt instructs the LLM to surface multiple
    matches verbatim and let the user pick — assert the skill output
    enables that disambiguation.
    """
    import respx
    from openvox.skills.builtin.google_workspace import ResolveContact

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        router.get(PEOPLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "person": {
                                "names": [{"displayName": "John Doe"}],
                                "emailAddresses": [
                                    {"value": "john@acme.com", "type": "work"}
                                ],
                                "organizations": [{"name": "Acme Inc"}],
                            }
                        },
                        {
                            "person": {
                                "names": [{"displayName": "John Smith"}],
                                "emailAddresses": [
                                    {"value": "john@beta.com", "type": "work"}
                                ],
                                "organizations": [{"name": "Beta Corp"}],
                            }
                        },
                    ]
                },
            )
        )

        result = await ResolveContact().run({"query": "John"}, _ctx())

    assert result["count"] == 2
    assert result["contacts"][0]["name"] == "John Doe"
    assert result["contacts"][0]["organizations"][0]["name"] == "Acme Inc"
    assert result["contacts"][1]["name"] == "John Smith"
    assert result["contacts"][1]["organizations"][0]["name"] == "Beta Corp"


@pytest.mark.asyncio
async def test_resolve_contact_skips_contacts_with_no_email(isolated_db):
    """A contact with phone + name but no email is filtered out.

    The skill exists to resolve name → EMAIL. Surfacing email-less
    entries would force the LLM to filter, which it sometimes
    forgets to do.
    """
    import respx
    from openvox.skills.builtin.google_workspace import ResolveContact

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        router.get(PEOPLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "person": {
                                "names": [{"displayName": "Phone-Only Friend"}],
                                "phoneNumbers": [{"value": "+1-555-1234"}],
                                # no emailAddresses
                            }
                        },
                        {
                            "person": {
                                "names": [{"displayName": "Real Match"}],
                                "emailAddresses": [{"value": "real@example.com"}],
                            }
                        },
                    ]
                },
            )
        )

        result = await ResolveContact().run({"query": "Friend"}, _ctx())

    assert result["count"] == 1
    assert result["contacts"][0]["name"] == "Real Match"


# ── Warmup path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_contact_warms_up_on_empty_then_retries(isolated_db):
    """Empty initial response triggers a warmup + one retry.

    People API's search index needs a warmup call before it returns
    relevant matches. The skill handles this inline: first call
    empty → fire warmup (query="") → retry → if that returns hits,
    return them.

    We assert the call sequence: search, warmup, search.
    """
    import respx
    from openvox.skills.builtin.google_workspace import ResolveContact

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=True) as router:
        # respx with a single route mock takes a list of responses.
        # Sequence: empty → warmup → real result.
        call_log: list[dict] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(str(request.url)).query)
            q = qs.get("query", [""])[0]
            call_log.append({"query": q})
            if len(call_log) == 1:
                # First search — empty (index cold).
                return httpx.Response(200, json={"results": []})
            elif len(call_log) == 2:
                # Warmup with empty query.
                assert q == "", f"expected warmup empty-query, got {q!r}"
                return httpx.Response(200, json={"results": []})
            else:
                # Retry search after warmup.
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "person": {
                                    "names": [{"displayName": "After Warmup"}],
                                    "emailAddresses": [
                                        {"value": "warm@example.com"}
                                    ],
                                }
                            }
                        ]
                    },
                )

        router.get(PEOPLE_SEARCH_URL).mock(side_effect=_handler)

        result = await ResolveContact().run({"query": "Warm"}, _ctx())

    # Three HTTP calls: search → warmup → search.
    assert len(call_log) == 3
    assert call_log[0]["query"] == "Warm"
    assert call_log[1]["query"] == ""
    assert call_log[2]["query"] == "Warm"
    assert result["count"] == 1
    assert result["contacts"][0]["name"] == "After Warmup"


# ── Error paths ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_contact_surfaces_403_as_actionable_hint(isolated_db):
    """403 from People API → readable "reconnect to grant scope" message.

    This is THE common failure mode in production: a user who
    connected before Phase 2 doesn't have ``contacts.readonly``
    in their stored scopes. They need to reconnect from the
    Integrations tab. We assert the error mentions BOTH the
    integration tab AND the specific scope so they know which
    button to click.
    """
    import respx
    from openvox.skills.builtin.google_workspace import ResolveContact

    await _connect_account("alice@example.com")

    with respx.mock(assert_all_called=False) as router:
        router.get(PEOPLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                403,
                json={
                    "error": {
                        "code": 403,
                        "message": "Request had insufficient authentication scopes.",
                        "status": "PERMISSION_DENIED",
                    }
                },
            )
        )

        with pytest.raises(RuntimeError) as ei:
            await ResolveContact().run({"query": "anyone"}, _ctx())

    msg = str(ei.value)
    # The actionable message we surface (not the raw Google JSON).
    assert "Integrations tab" in msg
    assert "contacts.readonly" in msg
    assert "reconnect" in msg.lower()


@pytest.mark.asyncio
async def test_resolve_contact_validates_query_required(isolated_db):
    from openvox.skills.builtin.google_workspace import ResolveContact

    await _connect_account("alice@example.com")
    with pytest.raises(ValueError, match="query is required"):
        await ResolveContact().run({}, _ctx())


@pytest.mark.asyncio
async def test_resolve_contact_no_account_connected(isolated_db):
    """Same auto-error as other native skills."""
    from openvox.skills.builtin.google_workspace import ResolveContact

    with pytest.raises(ValueError, match="No Google account is connected"):
        await ResolveContact().run({"query": "anyone"}, _ctx())
