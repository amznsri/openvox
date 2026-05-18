"""Skills for the Setup Assistant — voice-driven agent creation.

These six skills let the `setup-assistant` built-in agent walk a
non-technical user through creating *another* agent by voice. The
flow is roughly:

    1. user: "I run a salon and want to book appointments"
    2. assistant: `recommend_template(description)` → "receptionist"
    3. assistant: "I'll set you up with Receptionist — sound right?"
    4. user: "Yes, call it Acme Salon"
    5. assistant: `instantiate_template("receptionist", "Acme Salon")`
       → draft_agent_id stashed in ctx.metadata
    6. assistant: "Great. What greeting should it use?"
    7. user: "Hi, welcome to Acme..."
    8. assistant: `update_agent_field("greeting", "Hi, welcome to Acme...")`
    9. ...iterate over voice-friendly fields...
   10. assistant: `describe_remaining_setup()` →
       "These 3 fields still need manual setup: Twilio phone number, ..."
   11. user: "OK publish it"
   12. assistant: `publish_agent()`

Design constraints:
  - The skills mutate *the user's draft agent*, not the
    Setup Assistant agent itself. The draft's id lives on
    `ctx.metadata["draft_agent_id"]`.
  - `update_agent_field` validates the field name against a fixed
    allow-list (`_VOICE_EDITABLE_FIELDS`). The LLM can't accidentally
    write to `mcp_servers`, `channels`, `status`, or the dozens of
    other columns — sensitive fields stay form-only by design.
  - All skills emit verbose return blobs (with `field`, `value`,
    `agent_id`) so the assistant can read back what just happened
    ("OK, I set the greeting to ..."). This trades a turn for
    catching mishears before they accumulate.
"""

from __future__ import annotations

import logging
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext

logger = logging.getLogger(__name__)


# ── Draft persistence helpers ───────────────────────────────────────
# The Setup Assistant runs as a normal agent over either voice (/ws/voice)
# or text (/agents/{id}/turn) — sometimes the user starts in voice and
# switches to text mid-flow. To keep "which agent am I drafting?" consistent
# across both transports, we stash it on the Setup Assistant agent's own
# `channels.setup_state` JSON column rather than the SkillContext's
# (per-runner, ephemeral) metadata dict.


async def _get_draft_id(ctx: SkillContext) -> str:
    """Resolve the in-progress draft agent id for this Setup Assistant.

    Falls back to ctx.metadata when there's no agent_id (unit tests /
    `/skills/invoke` direct calls). Production flows always have one.
    """
    if not ctx.agent_id:
        return ctx.metadata.get("draft_agent_id") or ""
    from openvox.db import db_session
    from openvox.db.models import Agent

    async with db_session() as s:
        a = await s.get(Agent, ctx.agent_id)
        if a is None:
            return ""
        state = ((a.channels or {}).get("setup_state") or {}) if isinstance(a.channels, dict) else {}
        return str(state.get("draft_agent_id") or "")


async def _set_draft_id(ctx: SkillContext, draft_id: str) -> None:
    """Persist the draft id on the Setup Assistant agent's channels JSON."""
    # Always mirror to ctx.metadata so same-runner skills work even
    # without a DB round-trip.
    ctx.metadata["draft_agent_id"] = draft_id
    if not ctx.agent_id:
        return
    from openvox.db import db_session
    from openvox.db.models import Agent

    async with db_session() as s:
        a = await s.get(Agent, ctx.agent_id)
        if a is None:
            return
        channels = dict(a.channels or {})
        state = dict(channels.get("setup_state") or {})
        if draft_id:
            state["draft_agent_id"] = draft_id
        else:
            state.pop("draft_agent_id", None)
        channels["setup_state"] = state
        a.channels = channels


# Fields the Setup Assistant is allowed to write via voice. Anything
# outside this set is rejected by `update_agent_field` — keeps the
# LLM from accidentally injecting into channels / mcp_servers / status
# even when a user says something ambiguous.
_VOICE_EDITABLE_FIELDS: set[str] = {
    "name",
    "description",
    "greeting",
    "system_prompt",
    "voice_id",
    "voice_language",
    "voice_speed",
    "temperature",
    "max_tokens",
    "skills",     # list[str] of skill ids
    "voice_map",  # dict[str, str] for multilingual agents
}


# ── List / recommend templates ──────────────────────────────────────


class ListTemplatesSkill(BaseSkill):
    id = "list_templates"
    display_name = "List templates"
    description = (
        "Return the catalogue of agent templates the user can start "
        "from. Optionally filter by category (e.g. 'support', "
        "'sales') or language code (e.g. 'en', 'zh'). Call this when "
        "the user asks 'what templates are there?' or before "
        "recommending one."
    )
    parameters = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Optional substring filter on the category."},
            "language": {"type": "string", "description": "Optional substring filter on the language code (e.g. 'en')."},
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.api.routes.templates import TEMPLATES

        cat_f = (args.get("category") or "").strip().lower()
        lang_f = (args.get("language") or "").strip().lower()
        out: list[dict[str, Any]] = []
        for t in TEMPLATES:
            if cat_f and cat_f not in (t.get("category") or "").lower():
                continue
            if lang_f and lang_f not in (t.get("language") or t.get("id") or "").lower():
                continue
            out.append({
                "id": t["id"],
                "name": t.get("name") or t["id"],
                "category": t.get("category") or "",
                "tagline": t.get("tagline") or "",
                "language": t.get("language") or "en",
            })
        # Cap so the LLM doesn't try to read back 29+ entries on a wide
        # query — at that point ask the user to narrow.
        return {"count": len(out), "templates": out[:12], "truncated": len(out) > 12}


class RecommendTemplateSkill(BaseSkill):
    id = "recommend_template"
    display_name = "Recommend a template"
    description = (
        "Given a free-text description of what the user wants their "
        "agent to do, return the best-matching template_id. Use this "
        "before calling instantiate_template so the LLM can read it "
        "back to the user for confirmation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "What the user wants the agent to do, in their own words.",
            },
        },
        "required": ["description"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.api.routes.templates import TEMPLATES

        # Cheap keyword classifier — production deployments can swap
        # for an LLM call, but the keyword path keeps the recommendation
        # itself cheap (the calling LLM already paid for the user
        # description; no need to spend another round-trip just to
        # classify into ~30 templates).
        desc = (args.get("description") or "").lower()
        # Keyword → template_id substring map. Order matters; first
        # hit wins. Cover the common phrasings; the assistant prompt
        # tells the LLM to clarify when no strong match.
        rules: list[tuple[list[str], str]] = [
            (["appoint", "book", "salon", "barber", "spa", "receptionist", "schedul"], "receptionist"),
            (["lead", "sdr", "outbound", "qualif", "cold call", "telesales"], "sales-sdr"),
            (["order", "shipment", "refund", "return", "e-comm", "ecommerce", "shopping"], "ecommerce-support"),
            (["stock", "share price", "ticker", "market", "trading", "invest"], "stock-analyst"),
            (["pdf", "document", "knowledge base", "rag", "research", "policy"], "document-qa"),
            (["audio", "recording", "transcribe", "sentiment", "call analy"], "voice-analyzer"),
            (["tutor", "teach", "homework", "math", "science", "explain"], "education-tutor"),
            (["multilingual", "language", "polyglot", "international", "english spanish", "chinese support"], "multilingual-support"),
            (["customer service hotline", "hotline", "service line", "help line"], "hotline-en"),
            (["reactivat", "win back", "lapsed customer", "churn"], "reactivation-en"),
        ]

        matched_id = ""
        match_reason = ""
        for keywords, tid in rules:
            for kw in keywords:
                if kw in desc:
                    matched_id = tid
                    match_reason = f"matched keyword '{kw}'"
                    break
            if matched_id:
                break

        # Resolve to a real template — fall back to ecommerce-support
        # as a generic chatty agent when nothing matches. The assistant
        # prompt tells the LLM to clarify rather than commit when
        # confidence is low.
        ids = {t["id"] for t in TEMPLATES}
        if matched_id not in ids:
            # Search through templates for the matched id as suffix
            # (the language-suffixed ones like "hotline-en" match
            # via the rule above already).
            matched = next((t["id"] for t in TEMPLATES if t["id"] == matched_id), "")
            matched_id = matched

        if not matched_id:
            return {
                "template_id": "",
                "confidence": 0.0,
                "reasoning": "no strong keyword match — ask the user to clarify the use case",
            }

        tpl = next((t for t in TEMPLATES if t["id"] == matched_id), {})
        return {
            "template_id": matched_id,
            "name": tpl.get("name") or matched_id,
            "tagline": tpl.get("tagline") or "",
            "confidence": 0.7,
            "reasoning": match_reason,
        }


# ── Instantiate + mutate the draft ──────────────────────────────────


class InstantiateTemplateSkill(BaseSkill):
    id = "instantiate_template"
    display_name = "Instantiate a template"
    description = (
        "Create a new draft agent from the named template. Always "
        "confirm both the template_id and the name with the user "
        "before calling — this writes to the database. The draft "
        "id is stashed so the other setup skills can mutate it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "template_id": {"type": "string", "description": "Must be one returned by list_templates."},
            "name": {"type": "string", "description": "What to call the new agent."},
        },
        "required": ["template_id", "name"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.api.routes.templates import TEMPLATES
        from openvox.db import db_session
        from openvox.db.models import Agent

        template_id = (args.get("template_id") or "").strip()
        name = (args.get("name") or "").strip()
        if not template_id:
            return {"error": "template_id is required"}
        if not name:
            return {"error": "name is required"}

        tpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
        if tpl is None:
            return {
                "error": f"template '{template_id}' not found — call list_templates and pick from the catalogue",
            }

        defaults = dict(tpl.get("default") or {})
        # Always override the template's own example name with what
        # the user just said.
        defaults["name"] = name
        defaults["template_id"] = template_id
        # Mirror the regular template-instantiate route's behaviour:
        # pull llm_model + voice_id from .env defaults when the
        # template didn't pin them itself. Without this, agents
        # created by the Setup Assistant land with empty `llm_model`
        # and `voice_id` columns — they still WORK (the provider
        # falls back to settings at call time), but the dashboard
        # form shows a blank field which confuses users.
        from openvox.config import get_settings
        settings = get_settings()
        if not defaults.get("llm_model"):
            defaults["llm_model"] = settings.byteplus_llm_model
        if not defaults.get("voice_id"):
            defaults["voice_id"] = settings.byteplus_tts_default_voice

        async with db_session() as s:
            a = Agent(**{k: v for k, v in defaults.items() if hasattr(Agent, k)})
            s.add(a)
            await s.flush()
            agent_id = a.id

        # Stash on both ctx.metadata (fast path) AND
        # Agent.channels.setup_state (so voice + text turns share state).
        await _set_draft_id(ctx, agent_id)

        logger.info("setup-assistant: instantiated draft agent %s from %s", agent_id, template_id)
        return {
            "agent_id": agent_id,
            "name": name,
            "template_id": template_id,
            "next": "Use update_agent_field to set greeting / prompt / voice etc., then publish_agent when done.",
        }


class UpdateAgentFieldSkill(BaseSkill):
    id = "update_agent_field"
    display_name = "Update an agent field"
    description = (
        "Patch a single field on the draft agent. ONLY the following "
        "fields are voice-editable: " + ", ".join(sorted(_VOICE_EDITABLE_FIELDS)) + ". "
        "Sensitive fields (API keys, MCP server tokens, phone numbers, "
        "channels) stay form-only and must NOT be set via this skill. "
        "Always read the value back to the user after writing — "
        "voice mishears compound otherwise."
    )
    parameters = {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "One of: " + ", ".join(sorted(_VOICE_EDITABLE_FIELDS)),
            },
            "value": {
                "description": (
                    "New value. String for prompt/greeting/voice_id; "
                    "number for temperature/max_tokens; list[str] for "
                    "skills; dict[str,str] for voice_map."
                ),
            },
        },
        "required": ["field", "value"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.db import db_session
        from openvox.db.models import Agent

        agent_id = await _get_draft_id(ctx)
        if not agent_id:
            return {
                "error": "no draft agent yet — call instantiate_template first",
            }

        field = (args.get("field") or "").strip()
        if field not in _VOICE_EDITABLE_FIELDS:
            return {
                "error": (
                    f"field '{field}' isn't voice-editable. Allowed fields: "
                    + ", ".join(sorted(_VOICE_EDITABLE_FIELDS))
                    + ". Tell the user this needs to be set manually on the agent page."
                ),
            }

        value = args.get("value")
        # Light type coercion so the LLM can pass strings even for
        # numeric fields without us 500-ing.
        if field in {"temperature", "voice_speed"} and isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                return {"error": f"value for '{field}' must be a number"}
        if field == "max_tokens" and isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                return {"error": "value for 'max_tokens' must be an integer"}

        async with db_session() as s:
            a = await s.get(Agent, agent_id)
            if a is None:
                # Stash is stale — somebody deleted the agent.
                await _set_draft_id(ctx, "")
                return {"error": "draft agent has been deleted; start over with instantiate_template"}
            setattr(a, field, value)
            await s.flush()

        # Return a verbose blob so the assistant can read it back.
        return {"updated": True, "agent_id": agent_id, "field": field, "value": value}


# ── Publish + describe-remaining ────────────────────────────────────


class PublishDraftSkill(BaseSkill):
    id = "publish_agent"
    display_name = "Publish the draft agent"
    description = (
        "Flip the draft from 'draft' to 'published' status. Only call "
        "this AFTER reading back the final config and getting "
        "confirmation from the user — publishing is reversible but "
        "we want the user to feel in control."
    )
    parameters = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.db import db_session
        from openvox.db.models import Agent, AgentStatus

        agent_id = await _get_draft_id(ctx)
        if not agent_id:
            return {"error": "no draft agent — instantiate_template first"}

        async with db_session() as s:
            a = await s.get(Agent, agent_id)
            if a is None:
                await _set_draft_id(ctx, "")
                return {"error": "draft agent deleted; start over"}
            a.status = AgentStatus.PUBLISHED.value
            await s.flush()

        # Clear the draft pointer so the next user starts a fresh session
        # cleanly. The published agent itself isn't deleted — they can
        # always reopen it from /dashboard/agents to keep editing.
        await _set_draft_id(ctx, "")

        logger.info("setup-assistant: published agent %s", agent_id)
        return {
            "published": True,
            "agent_id": agent_id,
            "next": "Tell the user the agent is live and where to find it.",
        }


class DescribeRemainingSetupSkill(BaseSkill):
    id = "describe_remaining_setup"
    display_name = "Describe remaining manual setup"
    description = (
        "Return the list of fields that still need manual configuration "
        "on the dashboard (tokens, phone numbers, MCP servers, channels). "
        "Call this near the end of the conversation so the user knows "
        "what they need to click before the agent is fully functional."
    )
    parameters = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.db import db_session
        from openvox.db.models import Agent

        agent_id = await _get_draft_id(ctx)
        if not agent_id:
            return {"error": "no draft agent yet"}

        async with db_session() as s:
            a = await s.get(Agent, agent_id)
            if a is None:
                return {"error": "draft agent has been deleted"}
            channels = a.channels or {}
            mcp_servers = a.mcp_servers or []

        # Voice-hostile fields the assistant should defer to the form.
        # Logic: only list items the user *probably* cares about given
        # the agent's skills. e.g. an agent without Twilio in its
        # channels doesn't need a Twilio phone number reminder.
        required: list[dict[str, str]] = []
        skill_ids = set(a.skills or [])
        # Always-relevant manual items.
        if not channels.get("telegram"):
            required.append({
                "label": "Telegram bot",
                "why": "If you want users to chat with this agent via Telegram, run the Connect-Telegram wizard.",
                "where_to_set": "Agent → Channels tab → Connect Telegram",
            })
        if any("twilio" in (s_id or "").lower() for s_id in skill_ids) or "outbound_call_batch" in skill_ids:
            required.append({
                "label": "Twilio phone number",
                "why": "Required to receive inbound calls or place outbound ones.",
                "where_to_set": "Set TWILIO_* in .env, then add the phone number to Agent.channels.twilio.phone_numbers.",
            })
        if not mcp_servers:
            required.append({
                "label": "MCP servers (optional)",
                "why": "Drop in Slack, GitHub, HubSpot, etc. for external tool access.",
                "where_to_set": "Agent → MCP tab → Browse catalogue",
            })
        # The agent's voice may need activation on the user's BytePlus key.
        required.append({
            "label": "Voice activation (BytePlus)",
            "why": (
                "BytePlus TTS voices must be activated on your key. "
                f"This agent uses voice_id='{a.voice_id}' — if you hear a TTS error on first test, "
                "activate that voice in the BytePlus console or pick a different one on the Voice tab."
            ),
            "where_to_set": "BytePlus console → Voice → Activate, or Agent → Voice & model tab",
        })

        return {
            "agent_id": agent_id,
            "required": required,
            "next": (
                "Read the list back to the user, then offer to publish "
                "if they're ready to test the agent as-is."
            ),
        }


SKILLS = [
    ListTemplatesSkill,
    RecommendTemplateSkill,
    InstantiateTemplateSkill,
    UpdateAgentFieldSkill,
    PublishDraftSkill,
    DescribeRemainingSetupSkill,
]
