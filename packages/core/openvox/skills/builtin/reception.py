"""Skills for the Receptionist / Appointment-Scheduler template.

Backed by a tiny in-memory calendar so the demo works out of the box.
In production you'd swap these implementations for Google Calendar,
Microsoft Bookings, Calendly, etc. — the skill *interface* stays the
same so the agent prompt doesn't have to change.

Demo dataset:
  - 9 AM–6 PM Mon–Fri, closed weekends.
  - The next 14 days have hourly slots; some pre-booked so the agent
    can demo "Tuesday at 2 PM is taken, how about 3 PM?".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext


# ── Demo calendar ────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_BUSINESS = {
    "name": "Acme Salon & Spa",
    "phone": "+1 (555) 123-4567",
    "address": "221B Baker Street, San Francisco, CA 94102",
    "hours": {  # 24-h ISO weekday number (1=Mon … 7=Sun) → (open, close) or None
        1: ("09:00", "18:00"),
        2: ("09:00", "18:00"),
        3: ("09:00", "18:00"),
        4: ("09:00", "18:00"),
        5: ("09:00", "18:00"),
        6: None,
        7: None,
    },
    "services": [
        {"id": "haircut", "name": "Haircut", "duration_min": 60, "price_usd": 65},
        {"id": "color", "name": "Hair colour", "duration_min": 120, "price_usd": 180},
        {"id": "manicure", "name": "Manicure", "duration_min": 45, "price_usd": 45},
        {"id": "massage", "name": "60-min Swedish massage", "duration_min": 60, "price_usd": 120},
    ],
}


def _seed_appointments() -> dict[str, dict[str, Any]]:
    """Pre-fill a couple of slots so the agent can demonstrate clashes."""
    now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict[str, dict[str, Any]] = {}

    def book(day_offset: int, hour: int, service: str, customer: str) -> None:
        start = (now + timedelta(days=day_offset)).replace(hour=hour)
        appt_id = f"APT-{day_offset}-{hour}"
        out[appt_id] = {
            "id": appt_id,
            "customer_name": customer,
            "service_id": service,
            "start": start.isoformat(),
            "end": (start + timedelta(hours=1)).isoformat(),
            "status": "confirmed",
        }

    # A few pre-existing bookings for the next few weekdays.
    book(1, 14, "haircut", "Existing Client A")
    book(1, 16, "manicure", "Existing Client B")
    book(2, 10, "color", "Existing Client C")
    return out


_APPOINTMENTS: dict[str, dict[str, Any]] = _seed_appointments()


# ── Helpers ──────────────────────────────────────────────────────


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _hours_for(weekday: int) -> tuple[str, str] | None:
    return _BUSINESS["hours"].get(weekday)


def _is_open(dt: datetime) -> bool:
    hours = _hours_for(dt.isoweekday())
    if hours is None:
        return False
    open_h, close_h = hours
    return open_h <= dt.strftime("%H:%M") < close_h


def _slot_taken(start: datetime, duration_min: int) -> bool:
    end = start + timedelta(minutes=duration_min)
    for appt in _APPOINTMENTS.values():
        if appt["status"] != "confirmed":
            continue
        s, e = _parse_iso(appt["start"]), _parse_iso(appt["end"])
        if not s or not e:
            continue
        # Any overlap?
        if start < e and end > s:
            return True
    return False


# ── Skills ───────────────────────────────────────────────────────


class BusinessInfo(BaseSkill):
    id = "business_info"
    display_name = "Look up business info"
    description = "Return the business name, phone, address, hours, and services."
    parameters = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        return {
            "name": _BUSINESS["name"],
            "phone": _BUSINESS["phone"],
            "address": _BUSINESS["address"],
            "hours": _BUSINESS["hours"],
            "services": _BUSINESS["services"],
        }


class CheckAvailability(BaseSkill):
    id = "check_availability"
    display_name = "Check open slots"
    description = (
        "Find available appointment slots for a service over the next N days. "
        "Returns ISO timestamps; the agent should read them back in a human-friendly "
        "way (e.g. 'Tuesday at 2 PM')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "service_id": {"type": "string", "enum": ["haircut", "color", "manicure", "massage"]},
            "days_ahead": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 6},
        },
        "required": ["service_id"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        service_id = (args.get("service_id") or "").strip()
        days_ahead = int(args.get("days_ahead") or 7)
        limit = int(args.get("limit") or 6)

        service = next((s for s in _BUSINESS["services"] if s["id"] == service_id), None)
        if service is None:
            return {"error": f"unknown service: {service_id}"}

        duration = service["duration_min"]
        now = _utcnow().replace(minute=0, second=0, microsecond=0)
        candidates: list[str] = []

        for day in range(days_ahead + 1):
            d = now + timedelta(days=day)
            hours = _hours_for(d.isoweekday())
            if hours is None:
                continue
            open_h, close_h = hours
            open_dt = d.replace(hour=int(open_h.split(":")[0]))
            close_dt = d.replace(hour=int(close_h.split(":")[0]))
            slot = open_dt
            while slot + timedelta(minutes=duration) <= close_dt:
                if slot > now and not _slot_taken(slot, duration):
                    candidates.append(slot.isoformat())
                    if len(candidates) >= limit:
                        return {"service": service, "slots": candidates}
                slot += timedelta(minutes=60)  # 1-hour grid
        return {"service": service, "slots": candidates}


class BookAppointment(BaseSkill):
    id = "book_appointment"
    display_name = "Book an appointment"
    description = (
        "Book a slot for a customer. Always confirm the customer's name, phone, "
        "service, and the *exact* start time before calling this — the slot must "
        "be one returned by check_availability."
    )
    parameters = {
        "type": "object",
        "properties": {
            "customer_name": {"type": "string"},
            "phone": {"type": "string"},
            "service_id": {"type": "string"},
            "start": {"type": "string", "description": "ISO datetime, e.g. 2026-05-12T14:00:00+00:00"},
            "notes": {"type": "string"},
        },
        "required": ["customer_name", "phone", "service_id", "start"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        name = (args.get("customer_name") or "").strip()
        phone = (args.get("phone") or "").strip()
        service_id = (args.get("service_id") or "").strip()
        start_iso = (args.get("start") or "").strip()
        notes = (args.get("notes") or "").strip()

        if not (name and phone and service_id and start_iso):
            return {"error": "customer_name, phone, service_id, and start are all required"}
        service = next((s for s in _BUSINESS["services"] if s["id"] == service_id), None)
        if service is None:
            return {"error": f"unknown service: {service_id}"}
        start = _parse_iso(start_iso)
        if start is None:
            return {"error": f"could not parse start time: {start_iso!r}"}
        if not _is_open(start):
            return {"error": "we're closed at that time", "hours": _BUSINESS["hours"]}
        if _slot_taken(start, service["duration_min"]):
            return {"error": "that slot is already booked"}

        appt_id = f"APT-{uuid.uuid4().hex[:8].upper()}"
        end = start + timedelta(minutes=service["duration_min"])
        _APPOINTMENTS[appt_id] = {
            "id": appt_id,
            "customer_name": name,
            "phone": phone,
            "service_id": service_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "notes": notes,
            "status": "confirmed",
        }
        return {
            "ok": True,
            "appointment_id": appt_id,
            "confirmation": (
                f"Booked: {service['name']} for {name} on "
                f"{start.strftime('%A %B %d at %I:%M %p UTC')}. "
                f"Confirmation code {appt_id}."
            ),
            "details": _APPOINTMENTS[appt_id],
        }


class CancelAppointment(BaseSkill):
    id = "cancel_appointment"
    display_name = "Cancel an appointment"
    description = "Cancel a previously booked appointment by its confirmation id."
    parameters = {
        "type": "object",
        "properties": {
            "appointment_id": {"type": "string"},
        },
        "required": ["appointment_id"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        appt_id = (args.get("appointment_id") or "").strip().upper()
        if appt_id not in _APPOINTMENTS:
            return {"error": "no such appointment", "appointment_id": appt_id}
        _APPOINTMENTS[appt_id]["status"] = "cancelled"
        return {"ok": True, "appointment_id": appt_id, "status": "cancelled"}


class ListAppointments(BaseSkill):
    id = "list_appointments"
    display_name = "List today's appointments"
    description = (
        "List all confirmed appointments scheduled today (UTC). Useful when "
        "the user asks 'what's on the schedule today?'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "day_offset": {
                "type": "integer",
                "default": 0,
                "description": "0 for today, 1 for tomorrow, -1 for yesterday, etc.",
            },
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        day_offset = int(args.get("day_offset") or 0)
        target = (_utcnow() + timedelta(days=day_offset)).date()
        same_day: list[dict] = []
        for appt in _APPOINTMENTS.values():
            s = _parse_iso(appt["start"])
            if s is None:
                continue
            if s.date() == target and appt["status"] == "confirmed":
                same_day.append(appt)
        same_day.sort(key=lambda a: a["start"])
        return {"date": target.isoformat(), "count": len(same_day), "appointments": same_day}


SKILLS = [BusinessInfo, CheckAvailability, BookAppointment, CancelAppointment, ListAppointments]
