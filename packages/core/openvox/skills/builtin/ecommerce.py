"""Skills for the e-commerce customer-support template.

These are demo implementations — real installations would back them with
the merchant's order DB / OMS. The shapes are stable so a deployment
can swap the implementation without touching the agent prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext


# A tiny in-memory demo store so the agent has something to "look up".
_DEMO_ORDERS: dict[str, dict[str, Any]] = {
    "ORD-1001": {
        "status": "shipped",
        "carrier": "DHL",
        "tracking": "JD0011223344",
        "eta": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "items": [{"sku": "SKU-A", "name": "Wireless headphones", "qty": 1}],
    },
    "ORD-1002": {
        "status": "processing",
        "items": [{"sku": "SKU-B", "name": "Laptop stand", "qty": 1}],
    },
}


class LookupOrder(BaseSkill):
    id = "lookup_order"
    display_name = "Look up order"
    description = "Look up an order by ID and return status + tracking info."
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "The customer's order ID"},
        },
        "required": ["order_id"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        oid = (args.get("order_id") or "").strip().upper()
        if oid in _DEMO_ORDERS:
            return {"order_id": oid, **_DEMO_ORDERS[oid]}
        return {"error": "order not found", "order_id": oid}


class StartReturn(BaseSkill):
    id = "start_return"
    display_name = "Start a return"
    description = "Begin a return for an order."
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["order_id", "reason"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        oid = (args.get("order_id") or "").strip().upper()
        return {
            "ok": True,
            "rma": f"RMA-{oid}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            "label_url": f"https://example.com/rma/{oid}.pdf",
            "instructions": "Drop the package at any DHL pickup point within 14 days.",
        }


class CheckStock(BaseSkill):
    id = "check_stock"
    display_name = "Check stock"
    description = "Check inventory level for a SKU."
    parameters = {
        "type": "object",
        "properties": {"sku": {"type": "string"}},
        "required": ["sku"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        sku = (args.get("sku") or "").strip().upper()
        # Pretend we have inventory data.
        return {"sku": sku, "in_stock": 24, "warehouse": "AMS-1"}


SKILLS = [LookupOrder, StartReturn, CheckStock]
