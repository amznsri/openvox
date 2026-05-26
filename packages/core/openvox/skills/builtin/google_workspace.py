"""Native Gmail + Calendar skills (Phase 1.4).

Each skill talks directly to the Google API via HTTPS — no MCP server
subprocess, no `gcp-oauth.keys.json` file, no Cloud Console
expedition for the user. The auth path is:

  user clicks Connect Gmail on the dashboard
    → /api/v1/integrations/google/start          [Phase 1.2]
    → Google consent screen
    → /oauth/google/callback                     [Phase 1.2]
    → oauth.store.set_oauth_token(provider=google, user_email=…)
                                                  [Phase 1.3]
    → THESE SKILLS read tokens via ensure_fresh_access_token(…)

This module replaces the MCP-based path that templates used in
Sessions 16-17 (which required the user to provision their own
Google Cloud OAuth client + paste credentials into the MCP tab).
The MCP path stays available as a power-user alternative per the
Session 18 decision baked into PLANNING_SESSION18.md §1 — but every
template now defaults to these native skills.

Multi-account model.
  Phase 1.3's token store uses a composite PK ``(provider, user_email)``,
  so a single OpenVox user can connect both personal@gmail.com AND
  work@gmail.com. Each skill takes an optional ``user_email`` argument:

    * If omitted AND exactly one Google account is connected → use it.
    * If omitted AND multiple connected → error listing the choices.
    * If specified but not connected → error suggesting the dashboard.

  This is intentionally LLM-friendly: the LLM doesn't need to know
  the email up-front (humans speak in pronouns: "send John an email"),
  but it can disambiguate when there's more than one account.

Coverage today (Phase 1.4):

  - list_emails                  Gmail messages.list + metadata fetch
  - read_email                   Gmail messages.get full body
  - send_email                   Gmail messages.send (RFC 5322)
  - search_contacts_in_gmail     Header scrape across recent threads
  - list_calendar_events         Calendar events.list
  - create_calendar_event        Calendar events.insert
  - update_calendar_event        Calendar events.patch
  - delete_calendar_event        Calendar events.delete
  - find_free_time               Derived from events.list

Phase 2 will add ``resolve_contact`` (People API) which subsumes
``search_contacts_in_gmail`` for accounts that actually have
Contacts populated. We keep the Gmail-scrape variant because it
still works for accounts that don't.
"""

from __future__ import annotations

import base64
import email.mime.text
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from openvox.oauth import google as google_oauth
from openvox.oauth.store import list_oauth_integrations
from openvox.skills.base import BaseSkill, SkillContext
from openvox.utils.http import make_async_client

logger = logging.getLogger(__name__)


GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
PEOPLE_BASE = "https://people.googleapis.com/v1"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


# ── Shared helpers ────────────────────────────────────────────────


async def _resolve_user_email(maybe_email: str | None) -> str:
    """Pick the Google account this call should run against.

    Returns the email (lowercased) or raises ``ValueError`` with a
    message the LLM can relay to the user verbatim.
    """
    if maybe_email:
        return maybe_email.strip().lower()

    integrations = await list_oauth_integrations()
    google_only = [r for r in integrations if r.get("provider") == "google"]
    if not google_only:
        raise ValueError(
            "No Google account is connected. Open the dashboard and click "
            "Connect Gmail on the Integrations tab."
        )
    if len(google_only) == 1:
        return google_only[0]["user_email"]
    emails = ", ".join(r["user_email"] for r in google_only)
    raise ValueError(
        "Multiple Google accounts are connected — say which one to use. "
        f"Available: {emails}."
    )


async def _google_request(
    method: str,
    url: str,
    user_email: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    raw_content_type: str | None = None,
) -> dict[str, Any]:
    """Authenticated request to a Google API endpoint.

    Pulls a fresh access_token via the OAuth helper, sets the Bearer
    header, surfaces the parsed JSON body on success. On 401 we DO
    NOT retry — ``ensure_fresh_access_token`` already refreshed if
    needed, so a 401 means the token / scope is actually broken and
    the user needs to reconnect.
    """
    token = await google_oauth.ensure_fresh_access_token(user_email)
    headers = {"Authorization": f"Bearer {token}"}
    if raw_content_type:
        headers["Content-Type"] = raw_content_type
    async with make_async_client(timeout=20.0) as client:
        resp = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            content=raw_body,
            headers=headers,
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"raw": resp.text[:500]}
            raise RuntimeError(
                f"Google API {method} {url} failed (HTTP {resp.status_code}): {detail}"
            )
        if not resp.content:
            return {}
        # DELETE often returns 204 with no body.
        try:
            return resp.json()
        except Exception:
            return {}


def _decode_email_header(value: str) -> str:
    """RFC 2047-encoded header → plain text. Best-effort."""
    try:
        from email.header import decode_header

        parts = decode_header(value or "")
        out = []
        for fragment, encoding in parts:
            if isinstance(fragment, bytes):
                out.append(fragment.decode(encoding or "utf-8", errors="replace"))
            else:
                out.append(fragment)
        return "".join(out)
    except Exception:
        return value or ""


def _b64url_decode(s: str) -> bytes:
    """Gmail returns bodies as base64url WITHOUT padding."""
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def _extract_plaintext_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail message payload tree looking for text/plain.

    Strongly prefers ``text/plain``; only falls back to ``text/html``
    (with a crude tag strip) when no plaintext part exists anywhere
    in the tree. Returns empty string if neither.
    """
    import re

    def collect(part: dict[str, Any], plain: list[str], html: list[str]) -> None:
        mime = part.get("mimeType", "")
        body = (part.get("body") or {}).get("data") or ""
        if body and mime == "text/plain":
            plain.append(body)
        elif body and mime == "text/html":
            html.append(body)
        for child in part.get("parts") or []:
            collect(child, plain, html)

    plain: list[str] = []
    html: list[str] = []
    collect(payload, plain, html)

    if plain:
        mime, data = "text/plain", plain[0]
    elif html:
        mime, data = "text/html", html[0]
    else:
        return ""
    try:
        raw = _b64url_decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""
    if mime == "text/html":
        # Crude HTML → text — good enough for "what's the gist of this email".
        raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
        raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
    return raw


# ── Gmail skills ──────────────────────────────────────────────────


class ListEmails(BaseSkill):
    id = "list_emails"
    display_name = "List Gmail messages"
    description = (
        "List recent Gmail messages, optionally matching a Gmail search "
        "query (e.g. 'from:boss@company.com', 'is:unread', "
        "'subject:invoice newer_than:7d'). Returns message id, subject, "
        "from, and a snippet — call read_email to get the full body."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Gmail search query. Default empty (inbox). Supports "
                    "the same operators as the Gmail search box."
                ),
                "default": "",
            },
            "max_results": {
                "type": "integer",
                "description": "Cap on results returned (default 10, max 50).",
                "default": 10,
            },
            "user_email": {
                "type": "string",
                "description": (
                    "Which connected Google account to use. Omit when "
                    "only one is connected."
                ),
            },
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        query = (args.get("query") or "").strip()
        max_results = max(1, min(int(args.get("max_results") or 10), 50))

        listing = await _google_request(
            "GET",
            f"{GMAIL_BASE}/users/me/messages",
            email_addr,
            params={
                "q": query,
                "maxResults": max_results,
            },
        )
        ids = [m["id"] for m in listing.get("messages") or []]
        if not ids:
            return {"account": email_addr, "query": query, "count": 0, "messages": []}

        # Fetch metadata per message. We bound at max_results so this
        # is at worst 50 HTTP calls — Gmail allows much higher with
        # batching, but the metadata format isn't worth the extra
        # complexity here. The LLM rarely asks for >10 messages.
        messages = []
        for mid in ids:
            meta = await _google_request(
                "GET",
                f"{GMAIL_BASE}/users/me/messages/{mid}",
                email_addr,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "To", "Date"],
                },
            )
            headers = {
                h["name"]: _decode_email_header(h["value"])
                for h in (meta.get("payload") or {}).get("headers") or []
            }
            messages.append(
                {
                    "id": mid,
                    "thread_id": meta.get("threadId"),
                    "subject": headers.get("Subject", "(no subject)"),
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "date": headers.get("Date", ""),
                    "snippet": meta.get("snippet", ""),
                    "is_unread": "UNREAD" in (meta.get("labelIds") or []),
                }
            )
        return {
            "account": email_addr,
            "query": query,
            "count": len(messages),
            "messages": messages,
        }


class ReadEmail(BaseSkill):
    id = "read_email"
    display_name = "Read a Gmail message"
    description = (
        "Fetch the full body of one Gmail message by id. Returns "
        "headers + plaintext body (HTML fallback stripped to text)."
    )
    parameters = {
        "type": "object",
        "required": ["message_id"],
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Gmail message id, as returned by list_emails.",
            },
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        mid = (args.get("message_id") or "").strip()
        if not mid:
            raise ValueError("message_id is required")
        msg = await _google_request(
            "GET",
            f"{GMAIL_BASE}/users/me/messages/{mid}",
            email_addr,
            params={"format": "full"},
        )
        payload = msg.get("payload") or {}
        headers = {
            h["name"]: _decode_email_header(h["value"])
            for h in payload.get("headers") or []
        }
        body = _extract_plaintext_body(payload)
        return {
            "account": email_addr,
            "id": mid,
            "thread_id": msg.get("threadId"),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "labels": msg.get("labelIds") or [],
        }


class SendEmail(BaseSkill):
    id = "send_email"
    display_name = "Send a Gmail message"
    description = (
        "Send an email via the connected Gmail account. Provide recipient, "
        "subject, and body — optionally cc/bcc. The agent should confirm "
        "with the user before calling this skill (sending email is not "
        "reversible)."
    )
    parameters = {
        "type": "object",
        "required": ["to", "subject", "body"],
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address (or comma-separated list).",
            },
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Plain-text body."},
            "cc": {"type": "string"},
            "bcc": {"type": "string"},
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        to = (args.get("to") or "").strip()
        subject = (args.get("subject") or "").strip()
        body = args.get("body") or ""
        cc = (args.get("cc") or "").strip()
        bcc = (args.get("bcc") or "").strip()
        if not to:
            raise ValueError("to is required")
        if not subject:
            raise ValueError("subject is required")
        if not body:
            raise ValueError("body is required")

        # Build an RFC 5322 message. MIMEText handles encoding.
        msg = email.mime.text.MIMEText(body, _charset="utf-8")
        msg["To"] = to
        msg["Subject"] = subject
        msg["From"] = email_addr
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        sent = await _google_request(
            "POST",
            f"{GMAIL_BASE}/users/me/messages/send",
            email_addr,
            json_body={"raw": raw},
        )
        logger.info("sent gmail message from=%s to=%s id=%s",
                    email_addr, to, sent.get("id"))
        return {
            "account": email_addr,
            "id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": to,
            "subject": subject,
        }


class ResolveContact(BaseSkill):
    """Primary contact-resolution path: Google People API.

    Calls `people.searchContacts` with the user's free-text query
    (a name, partial name, or other hint) and returns the matched
    entries with name + email + photo. The Executive Assistant's
    prompt instructs the LLM to try this BEFORE
    `search_contacts_in_gmail` because People API knows about
    contacts the user has saved explicitly — including people
    they've never exchanged email with (e.g. "my dentist" saved in
    Contacts with phone + address but no email history).

    Three-tier fallback chain (orchestrated by the prompt, not by
    this skill — keep skills single-purpose):

      1. `resolve_contact`        — Phase 2's People API path.
      2. `search_contacts_in_gmail` — Phase 1's Gmail-history scrape;
                                       works for accounts that have
                                       email history but no Contacts.
      3. ask the user                — fallback when neither source
                                       produces a confident match.

    Scope quirk: People API requires
    ``https://www.googleapis.com/auth/contacts.readonly`` which was
    added to ``DEFAULT_SCOPES`` in Phase 2.1. Users connected via
    Phase 1 don't have it — the skill detects this 403 and returns
    a clean error pointing at the Integrations tab.

    Warm-up gotcha: People API's `searchContacts` runs against an
    in-memory index that needs to be primed before it returns
    relevant matches. Per Google's docs, calling with `query=""`
    once per session warms it up. To keep the skill self-contained
    we do the warmup INSIDE the call only when an initial search
    returns empty — that's the only case where warmup matters in
    practice. The retry costs ~200ms one time per cold session.
    """

    id = "resolve_contact"
    display_name = "Resolve a contact name to an email (People API)"
    description = (
        "Look up a contact in the user's Google Contacts via the People "
        "API. Returns name + email + photo for each match, sorted by "
        "relevance. Use this BEFORE search_contacts_in_gmail when "
        "resolving a name to an email — People API has explicit "
        "contact records (e.g. 'my dentist') that Gmail history alone "
        "won't surface."
    )
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Free-text query — name, partial name, or other "
                    "hint. e.g. 'John', 'John Doe', 'dentist'."
                ),
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "description": "Cap on results returned (default 10, max 30).",
            },
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        query = (args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        max_results = max(1, min(int(args.get("max_results") or 10), 30))

        try:
            results = await _people_search(email_addr, query, max_results)
        except RuntimeError as e:
            # Scope missing (403) is the most common cause — surface a
            # readable hint instead of the raw Google error text.
            if "PERMISSION_DENIED" in str(e) or "403" in str(e):
                raise RuntimeError(
                    "Google Contacts (People API) access not granted on this "
                    "integration. Open the dashboard Integrations tab, "
                    "disconnect, then reconnect to grant the new "
                    "contacts.readonly scope."
                ) from e
            raise

        # Empty results — try the warmup-then-retry path once.
        if not results:
            logger.info("people: empty result for %r, warming up index", query)
            await _people_warmup(email_addr)
            results = await _people_search(email_addr, query, max_results)

        return {
            "account": email_addr,
            "query": query,
            "count": len(results),
            "contacts": results,
        }


async def _people_search(
    user_email: str, query: str, max_results: int
) -> list[dict[str, Any]]:
    """One round-trip to ``people:searchContacts``.

    ``readMask`` is a Field Mask — the comma-separated list of person
    fields we want back. Adding fields here is cheap (just a wider
    response) so we ask for everything the LLM might find useful:
    canonical display name, email + type label, photo URL.
    """
    body = await _google_request(
        "GET",
        f"{PEOPLE_BASE}/people:searchContacts",
        user_email,
        params={
            "query": query,
            "pageSize": max_results,
            "readMask": "names,emailAddresses,photos,phoneNumbers,organizations",
        },
    )
    out: list[dict[str, Any]] = []
    for entry in body.get("results") or []:
        person = entry.get("person") or {}
        name = ""
        for n in person.get("names") or []:
            if n.get("displayName"):
                name = n["displayName"]
                break
        emails = [
            {"address": e.get("value"), "type": e.get("type", "")}
            for e in person.get("emailAddresses") or []
            if e.get("value")
        ]
        photo_url = ""
        for ph in person.get("photos") or []:
            if ph.get("url"):
                photo_url = ph["url"]
                break
        # An entry without ANY email is useless for "schedule meeting
        # with X" — skip it so the LLM doesn't have to filter.
        if not emails:
            continue
        out.append(
            {
                "name": name,
                "emails": emails,
                "photo_url": photo_url,
                # Useful when multiple people share a first name —
                # the org / phone fields help the LLM ask "did you
                # mean John from Acme or John from Beta?"
                "organizations": [
                    {"name": o.get("name"), "title": o.get("title")}
                    for o in person.get("organizations") or []
                    if o.get("name") or o.get("title")
                ],
            }
        )
    return out


async def _people_warmup(user_email: str) -> None:
    """Prime the People API search index for this user.

    Google's docs:
        "Before searching for contacts, the People API needs to warm
        up its search service. The warmup process can take up to 30
        seconds and only needs to happen once per user session."

    In practice the warmup is fast (~200ms) and just consists of
    calling ``searchContacts`` with an empty query. We swallow any
    error here — if the warmup fails the worst case is that the
    retry call still returns empty and the LLM falls through to the
    Gmail-history skill.
    """
    try:
        await _google_request(
            "GET",
            f"{PEOPLE_BASE}/people:searchContacts",
            user_email,
            params={
                "query": "",
                "pageSize": 1,
                "readMask": "names",
            },
        )
    except Exception as e:
        logger.warning("people warmup failed (non-fatal): %s", e)


class SearchContactsInGmail(BaseSkill):
    """Cheap-and-cheerful contact resolution via Gmail history.

    Walks recent threads matching the query (usually the contact's
    name), extracts ``From:`` headers, returns the unique emails
    seen. Useful when the user asks "email John about lunch" — we
    look John up in the Gmail history and resolve to an email.

    Phase 2 will add ``resolve_contact`` (People API) which is the
    proper fix; this skill stays around because not every user has
    Contacts populated. Sales / Exec Assistant templates use it.
    """

    id = "search_contacts_in_gmail"
    display_name = "Find a contact's email from Gmail history"
    description = (
        "Search the Gmail history for messages matching the query "
        "(usually a person's name), then return distinct sender "
        "emails found. Useful for resolving 'John' → 'john@…' before "
        "calling send_email."
    )
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text name or hint to scan Gmail for.",
            },
            "max_results": {
                "type": "integer",
                "description": "Cap on Gmail messages to scan (default 20).",
                "default": 20,
            },
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        import re

        email_addr = await _resolve_user_email(args.get("user_email"))
        query = (args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        max_results = max(1, min(int(args.get("max_results") or 20), 50))

        listing = await _google_request(
            "GET",
            f"{GMAIL_BASE}/users/me/messages",
            email_addr,
            params={"q": query, "maxResults": max_results},
        )
        ids = [m["id"] for m in listing.get("messages") or []]
        seen: dict[str, dict[str, Any]] = {}  # email_lower → {email, name, freq}
        addr_re = re.compile(r"<([^>]+)>|([^\s<>]+@[^\s<>]+)")
        for mid in ids:
            meta = await _google_request(
                "GET",
                f"{GMAIL_BASE}/users/me/messages/{mid}",
                email_addr,
                params={"format": "metadata", "metadataHeaders": ["From"]},
            )
            for h in (meta.get("payload") or {}).get("headers") or []:
                if h["name"].lower() != "from":
                    continue
                raw = _decode_email_header(h["value"])
                # "Name <email>" or just "email"
                m = addr_re.search(raw)
                if not m:
                    continue
                addr = (m.group(1) or m.group(2) or "").strip().lower()
                if not addr or addr == email_addr:
                    continue
                # Display name = everything before the <…>
                name = raw.split("<", 1)[0].strip().strip('"').strip()
                entry = seen.setdefault(addr, {"email": addr, "name": name, "frequency": 0})
                if name and len(name) > len(entry["name"]):
                    entry["name"] = name
                entry["frequency"] += 1
        results = sorted(seen.values(), key=lambda r: -r["frequency"])
        return {
            "account": email_addr,
            "query": query,
            "count": len(results),
            "contacts": results,
        }


# ── Calendar skills ───────────────────────────────────────────────


def _normalise_event_time(value: str) -> dict[str, str]:
    """Convert an ISO 8601 string into Calendar's start/end shape.

    Calendar accepts either ``{"date": "YYYY-MM-DD"}`` for all-day
    events or ``{"dateTime": "...", "timeZone": "..."}`` for timed.
    We default to timed UTC; the LLM can pass timezone strings.
    """
    if "T" not in value:
        return {"date": value}
    return {"dateTime": value, "timeZone": "UTC"}


class ListCalendarEvents(BaseSkill):
    id = "list_calendar_events"
    display_name = "List Google Calendar events"
    description = (
        "List upcoming Google Calendar events between two times. "
        "Returns event id, summary, start, end, attendees, location. "
        "Useful for 'what's on my calendar tomorrow' or 'find my "
        "next meeting with Alice'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "time_min": {
                "type": "string",
                "description": (
                    "ISO 8601 lower bound (default: now). e.g. "
                    "'2026-05-26T00:00:00Z'."
                ),
            },
            "time_max": {
                "type": "string",
                "description": "ISO 8601 upper bound (default: 14 days from now).",
            },
            "query": {
                "type": "string",
                "description": "Free-text search filter (matches summary/description).",
            },
            "max_results": {"type": "integer", "default": 25},
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        now = datetime.now(timezone.utc)
        time_min = args.get("time_min") or now.isoformat()
        time_max = args.get("time_max") or (now + timedelta(days=14)).isoformat()
        max_results = max(1, min(int(args.get("max_results") or 25), 100))

        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max_results,
        }
        if args.get("query"):
            params["q"] = args["query"]

        body = await _google_request(
            "GET",
            f"{CALENDAR_BASE}/calendars/primary/events",
            email_addr,
            params=params,
        )
        events = []
        for ev in body.get("items") or []:
            events.append(
                {
                    "id": ev.get("id"),
                    "summary": ev.get("summary", "(no title)"),
                    "description": ev.get("description", ""),
                    "start": (ev.get("start") or {}).get("dateTime")
                    or (ev.get("start") or {}).get("date"),
                    "end": (ev.get("end") or {}).get("dateTime")
                    or (ev.get("end") or {}).get("date"),
                    "location": ev.get("location", ""),
                    "attendees": [
                        {"email": a.get("email"), "status": a.get("responseStatus")}
                        for a in ev.get("attendees") or []
                    ],
                    "html_link": ev.get("htmlLink"),
                }
            )
        return {
            "account": email_addr,
            "count": len(events),
            "events": events,
        }


class CreateCalendarEvent(BaseSkill):
    id = "create_calendar_event"
    display_name = "Create a Google Calendar event"
    description = (
        "Create a new event on the connected Google Calendar (primary "
        "calendar). Confirm details with the user before calling — "
        "sending invites is not silently reversible. attendees should "
        "be email addresses."
    )
    parameters = {
        "type": "object",
        "required": ["summary", "start", "end"],
        "properties": {
            "summary": {"type": "string", "description": "Event title."},
            "description": {"type": "string"},
            "start": {
                "type": "string",
                "description": "ISO 8601 start. e.g. 2026-05-27T10:00:00Z.",
            },
            "end": {"type": "string", "description": "ISO 8601 end."},
            "location": {"type": "string"},
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Attendee email addresses.",
            },
            "send_updates": {
                "type": "string",
                "enum": ["all", "externalOnly", "none"],
                "default": "all",
                "description": "Whether to email invites to attendees.",
            },
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        body = {
            "summary": args["summary"],
            "start": _normalise_event_time(args["start"]),
            "end": _normalise_event_time(args["end"]),
        }
        if args.get("description"):
            body["description"] = args["description"]
        if args.get("location"):
            body["location"] = args["location"]
        if args.get("attendees"):
            body["attendees"] = [{"email": a} for a in args["attendees"]]

        send_updates = args.get("send_updates", "all")
        created = await _google_request(
            "POST",
            f"{CALENDAR_BASE}/calendars/primary/events",
            email_addr,
            params={"sendUpdates": send_updates},
            json_body=body,
        )
        logger.info(
            "created calendar event account=%s id=%s summary=%r",
            email_addr, created.get("id"), args["summary"],
        )
        return {
            "account": email_addr,
            "id": created.get("id"),
            "html_link": created.get("htmlLink"),
            "summary": created.get("summary"),
            "start": (created.get("start") or {}).get("dateTime")
            or (created.get("start") or {}).get("date"),
            "end": (created.get("end") or {}).get("dateTime")
            or (created.get("end") or {}).get("date"),
        }


class UpdateCalendarEvent(BaseSkill):
    id = "update_calendar_event"
    display_name = "Update a Google Calendar event"
    description = (
        "Patch an existing Google Calendar event. Provide event_id + "
        "any fields to change. Useful for 'move my 3pm meeting to 4pm'."
    )
    parameters = {
        "type": "object",
        "required": ["event_id"],
        "properties": {
            "event_id": {"type": "string"},
            "summary": {"type": "string"},
            "description": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "location": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "send_updates": {
                "type": "string",
                "enum": ["all", "externalOnly", "none"],
                "default": "all",
            },
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        event_id = (args.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        body: dict[str, Any] = {}
        for k in ("summary", "description", "location"):
            if args.get(k):
                body[k] = args[k]
        if args.get("start"):
            body["start"] = _normalise_event_time(args["start"])
        if args.get("end"):
            body["end"] = _normalise_event_time(args["end"])
        if args.get("attendees"):
            body["attendees"] = [{"email": a} for a in args["attendees"]]
        if not body:
            raise ValueError(
                "At least one field to update is required (summary, start, …)"
            )
        send_updates = args.get("send_updates", "all")
        updated = await _google_request(
            "PATCH",
            f"{CALENDAR_BASE}/calendars/primary/events/{event_id}",
            email_addr,
            params={"sendUpdates": send_updates},
            json_body=body,
        )
        return {
            "account": email_addr,
            "id": updated.get("id"),
            "html_link": updated.get("htmlLink"),
            "summary": updated.get("summary"),
            "start": (updated.get("start") or {}).get("dateTime")
            or (updated.get("start") or {}).get("date"),
            "end": (updated.get("end") or {}).get("dateTime")
            or (updated.get("end") or {}).get("date"),
        }


class DeleteCalendarEvent(BaseSkill):
    id = "delete_calendar_event"
    display_name = "Delete a Google Calendar event"
    description = (
        "Delete a Google Calendar event by id. Confirm with the user "
        "first — this cancels invitations on attendees' calendars."
    )
    parameters = {
        "type": "object",
        "required": ["event_id"],
        "properties": {
            "event_id": {"type": "string"},
            "send_updates": {
                "type": "string",
                "enum": ["all", "externalOnly", "none"],
                "default": "all",
            },
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        event_id = (args.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        send_updates = args.get("send_updates", "all")
        await _google_request(
            "DELETE",
            f"{CALENDAR_BASE}/calendars/primary/events/{event_id}",
            email_addr,
            params={"sendUpdates": send_updates},
        )
        logger.info("deleted calendar event account=%s id=%s", email_addr, event_id)
        return {"account": email_addr, "id": event_id, "deleted": True}


class FindFreeTime(BaseSkill):
    """Naïve free-time finder.

    Walks the events.list response between time_min and time_max,
    looking for gaps of at least ``duration_min`` minutes during the
    user's working hours. Returns up to N candidate start times.

    NOT the same as Calendar's freebusy.query (which works across
    multiple calendars and respects recurring patterns better). We
    use events.list because it's simpler and matches what the LLM
    typically wants: "find me a 30-min slot on Tuesday afternoon".
    """

    id = "find_free_time"
    display_name = "Find free slots on Google Calendar"
    description = (
        "Suggest free time slots on the connected Google Calendar "
        "between time_min and time_max. Returns up to N candidate "
        "start times of at least duration_min minutes each."
    )
    parameters = {
        "type": "object",
        "required": ["duration_min"],
        "properties": {
            "duration_min": {
                "type": "integer",
                "description": "Minimum slot length in minutes (e.g. 30).",
            },
            "time_min": {
                "type": "string",
                "description": "ISO 8601 search-window start (default: now).",
            },
            "time_max": {
                "type": "string",
                "description": "ISO 8601 search-window end (default: 7 days from now).",
            },
            "working_hours_start": {
                "type": "integer",
                "default": 9,
                "description": "Hour of day (0-23) free slots can start.",
            },
            "working_hours_end": {
                "type": "integer",
                "default": 18,
                "description": "Hour of day (0-23) free slots must end by.",
            },
            "max_results": {"type": "integer", "default": 5},
            "user_email": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        email_addr = await _resolve_user_email(args.get("user_email"))
        duration_min = int(args.get("duration_min") or 30)
        now = datetime.now(timezone.utc)
        time_min_str = args.get("time_min") or now.isoformat()
        time_max_str = args.get("time_max") or (now + timedelta(days=7)).isoformat()
        wh_start = int(args.get("working_hours_start") or 9)
        wh_end = int(args.get("working_hours_end") or 18)
        max_results = max(1, min(int(args.get("max_results") or 5), 20))

        events_resp = await _google_request(
            "GET",
            f"{CALENDAR_BASE}/calendars/primary/events",
            email_addr,
            params={
                "timeMin": time_min_str,
                "timeMax": time_max_str,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 250,
            },
        )

        # Build a sorted list of (start, end) busy intervals (UTC).
        busy: list[tuple[datetime, datetime]] = []
        for ev in events_resp.get("items") or []:
            s = (ev.get("start") or {}).get("dateTime")
            e = (ev.get("end") or {}).get("dateTime")
            if not s or not e:
                continue  # skip all-day events for now
            try:
                start_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(e.replace("Z", "+00:00"))
            except Exception:
                continue
            busy.append((start_dt, end_dt))
        busy.sort()

        # Walk the search window in 30-minute steps. For each candidate
        # start, check working hours + no overlap with any busy interval.
        start_dt = datetime.fromisoformat(time_min_str.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(time_max_str.replace("Z", "+00:00"))
        slots: list[dict[str, str]] = []
        cursor = start_dt
        step = timedelta(minutes=30)
        slot_len = timedelta(minutes=duration_min)
        while cursor + slot_len <= end_dt and len(slots) < max_results:
            slot_start = cursor
            slot_end = cursor + slot_len
            # Working hours (UTC) — naïve but matches Phase 1's intent.
            if not (wh_start <= slot_start.hour and slot_end.hour <= wh_end):
                cursor += step
                continue
            # Same UTC date for both ends.
            if slot_start.date() != slot_end.date():
                cursor += step
                continue
            overlap = any(b_start < slot_end and b_end > slot_start
                          for b_start, b_end in busy)
            if not overlap:
                slots.append(
                    {
                        "start": slot_start.isoformat(),
                        "end": slot_end.isoformat(),
                    }
                )
            cursor += step

        return {
            "account": email_addr,
            "duration_min": duration_min,
            "count": len(slots),
            "slots": slots,
        }


SKILLS = [
    ListEmails,
    ReadEmail,
    SendEmail,
    ResolveContact,           # Phase 2 — People API (preferred)
    SearchContactsInGmail,    # Phase 1 — Gmail-history scrape (fallback)
    ListCalendarEvents,
    CreateCalendarEvent,
    UpdateCalendarEvent,
    DeleteCalendarEvent,
    FindFreeTime,
]
