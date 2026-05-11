"""Skills for the Outbound Lead Qualifier (SDR) template.

BANT-style qualification flow:
  Budget, Authority, Need, Timeline → score 0-100 → disposition
    qualified (≥70): book a demo with an AE
    nurture   (40-69): schedule a follow-up
    unqualified (<40): mark closed-lost

The skills here use a tiny in-memory leads dataset so the demo works
without any CRM. Swap them for an MCP-based HubSpot/Salesforce server
or a thin native skill in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Tiny demo CRM ────────────────────────────────────────────────


_LEADS: dict[str, dict[str, Any]] = {
    "LEAD-001": {
        "id": "LEAD-001",
        "company": "Northwind Logistics",
        "contact_name": "Sara Patel",
        "title": "VP of Operations",
        "phone": "+14155550101",
        "email": "sara.patel@northwind.example",
        "industry": "Logistics",
        "size_employees": 320,
        "interest": "Voice agent for inbound shipment inquiries",
        "status": "new",
    },
    "LEAD-002": {
        "id": "LEAD-002",
        "company": "Acme Robotics",
        "contact_name": "Marcus Lee",
        "title": "Head of Customer Success",
        "phone": "+14155550102",
        "email": "marcus@acme-robotics.example",
        "industry": "Manufacturing",
        "size_employees": 90,
        "interest": "Tier-1 support automation",
        "status": "new",
    },
    "LEAD-003": {
        "id": "LEAD-003",
        "company": "Hillcrest Health",
        "contact_name": "Dr. Priya Shah",
        "title": "Clinic Director",
        "phone": "+14155550103",
        "email": "priya@hillcrest.example",
        "industry": "Healthcare",
        "size_employees": 45,
        "interest": "After-hours patient intake",
        "status": "new",
    },
}

# Per-lead disposition records produced by record_disposition.
_DISPOSITIONS: dict[str, dict[str, Any]] = {}


def _bant_score(b: int, a: int, n: int, t: int) -> int:
    """Weighted BANT score (0-100). Need + Timeline weigh slightly more
    than Budget + Authority — a hot need with no clear budget is still
    worth booking a demo."""
    return min(100, max(0, int(0.22 * b + 0.22 * a + 0.28 * n + 0.28 * t)))


def _bucket(score: int) -> str:
    if score >= 70:
        return "qualified"
    if score >= 40:
        return "nurture"
    return "unqualified"


# ── Skills ───────────────────────────────────────────────────────


class FetchNextLead(BaseSkill):
    id = "fetch_next_lead"
    display_name = "Fetch next lead"
    description = (
        "Return the next lead to call. Prefers leads in 'new' status — if the "
        "agent is mid-conversation it should NOT call this again until the "
        "current disposition is recorded."
    )
    parameters = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        # Cheapest "queue": first lead in status='new'.
        for lead in _LEADS.values():
            if lead["status"] == "new":
                return {"lead": lead}
        return {"lead": None, "message": "No more leads in the new queue."}


class GetLead(BaseSkill):
    id = "get_lead"
    display_name = "Look up a specific lead"
    description = "Look up a lead by id (e.g. when the user references LEAD-002)."
    parameters = {
        "type": "object",
        "properties": {"lead_id": {"type": "string"}},
        "required": ["lead_id"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        lid = (args.get("lead_id") or "").strip().upper()
        lead = _LEADS.get(lid)
        if lead is None:
            return {"error": "lead not found", "lead_id": lid}
        return {"lead": lead, "disposition": _DISPOSITIONS.get(lid)}


class RecordDisposition(BaseSkill):
    id = "record_disposition"
    display_name = "Record BANT score"
    description = (
        "Persist the outcome of a qualification call. Always include 1-3 sentences of "
        "notes summarising what the prospect said. The 4 BANT scores (0-100 each) "
        "produce an overall score and a bucket: qualified (≥70), nurture (40-69), "
        "or unqualified (<40)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "lead_id": {"type": "string"},
            "budget": {"type": "integer", "minimum": 0, "maximum": 100},
            "authority": {"type": "integer", "minimum": 0, "maximum": 100},
            "need": {"type": "integer", "minimum": 0, "maximum": 100},
            "timeline": {"type": "integer", "minimum": 0, "maximum": 100},
            "notes": {"type": "string"},
            "next_step": {
                "type": "string",
                "enum": ["book_demo", "nurture_email", "closed_lost"],
                "description": "What you intend to do next. The agent should usually book_demo for qualified leads.",
            },
        },
        "required": ["lead_id", "budget", "authority", "need", "timeline", "notes"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        lid = (args.get("lead_id") or "").strip().upper()
        lead = _LEADS.get(lid)
        if lead is None:
            return {"error": "lead not found", "lead_id": lid}

        b = int(args.get("budget") or 0)
        a = int(args.get("authority") or 0)
        n = int(args.get("need") or 0)
        t = int(args.get("timeline") or 0)
        score = _bant_score(b, a, n, t)
        bucket = _bucket(score)
        next_step = args.get("next_step") or (
            "book_demo" if bucket == "qualified" else
            "nurture_email" if bucket == "nurture" else
            "closed_lost"
        )

        record = {
            "id": f"DISP-{uuid.uuid4().hex[:8].upper()}",
            "lead_id": lid,
            "budget": b,
            "authority": a,
            "need": n,
            "timeline": t,
            "score": score,
            "bucket": bucket,
            "notes": (args.get("notes") or "").strip(),
            "next_step": next_step,
            "recorded_at": _utcnow().isoformat(),
            "recorded_by": ctx.agent_id or "scheduler",
        }
        _DISPOSITIONS[lid] = record
        # Flip lead status so fetch_next_lead moves on.
        lead["status"] = bucket
        return {"ok": True, "disposition": record, "lead": lead}


class QualifiedLeads(BaseSkill):
    id = "qualified_leads"
    display_name = "List qualified leads"
    description = "Return all leads that have been BANT-scored as qualified."
    parameters = {
        "type": "object",
        "properties": {
            "bucket": {
                "type": "string",
                "enum": ["qualified", "nurture", "unqualified", "all"],
                "default": "qualified",
            },
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        wanted = (args.get("bucket") or "qualified").lower()
        out = []
        for lid, d in _DISPOSITIONS.items():
            if wanted == "all" or d["bucket"] == wanted:
                out.append({"lead": _LEADS[lid], "disposition": d})
        out.sort(key=lambda x: x["disposition"]["score"], reverse=True)
        return {"bucket": wanted, "count": len(out), "leads": out}


class BookDemo(BaseSkill):
    """Reuses the receptionist's calendar. Asking the agent to know about
    *both* skill libraries lets the SDR hand off seamlessly to a calendar
    slot — like a human SDR would loop in an AE."""

    id = "book_demo"
    display_name = "Book a product demo"
    description = (
        "Book a demo with an AE after BANT qualification. Uses the same calendar as "
        "the receptionist template — call check_availability first to read back 2-3 "
        "open slots, then confirm name + phone, then call this with the EXACT ISO "
        "start time from check_availability."
    )
    parameters = {
        "type": "object",
        "properties": {
            "lead_id": {"type": "string"},
            "customer_name": {"type": "string"},
            "phone": {"type": "string"},
            "start": {"type": "string", "description": "ISO datetime from check_availability"},
            "notes": {"type": "string"},
        },
        "required": ["lead_id", "customer_name", "phone", "start"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        # Late-import the receptionist booker so the modules stay
        # independently shippable.
        from openvox.skills.builtin.reception import BookAppointment

        booker = BookAppointment()
        result = await booker.run(
            {
                "customer_name": args.get("customer_name", ""),
                "phone": args.get("phone", ""),
                "service_id": "massage",  # any 60-min slot; we just want the calendar
                "start": args.get("start", ""),
                "notes": f"Demo for lead {args.get('lead_id', '')} — {args.get('notes', '')}",
            },
            ctx,
        )
        if isinstance(result, dict) and result.get("ok"):
            lid = (args.get("lead_id") or "").strip().upper()
            if lid in _LEADS:
                _LEADS[lid]["status"] = "demo_booked"
            result["lead_id"] = lid
        return result


SKILLS = [FetchNextLead, GetLead, RecordDisposition, QualifiedLeads, BookDemo]
