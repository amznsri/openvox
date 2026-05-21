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
        import re

        from openvox.api.routes.templates import TEMPLATES

        # Templates carry their OWN keyword rules in `t["match"]` —
        # `{"priority": int, "keywords": [str, ...]}`. We walk
        # TEMPLATES sorted by priority ascending (lower = checked
        # first = more specific). When you add a new template, set
        # its `match` dict on the entry itself — no second list to
        # maintain here. Templates without a `match` field don't
        # auto-recommend and can only be reached via `list_templates`.
        #
        # ── Score-based matching ──────────────────────────────
        # The old logic was "first substring hit wins" — which fired
        # ecommerce-support on the description "search web and RETURN
        # top 10 news" because `return` is in the ecommerce keyword
        # list. Now we:
        #   1. Use \b word-boundary regex so 'return' doesn't match
        #      inside 'returning' (would still match 'return' alone).
        #   2. Count DISTINCT keyword hits per template.
        #   3. Confidence scales with hit count:
        #        ≥2 hits → 0.85   (high — recommend confidently)
        #        1 hit   → 0.4    (low  — surface but tell the LLM to
        #                          double-check or offer custom path)
        #        0 hits  → 0.0    (none — explicitly recommend
        #                          create_custom_agent)
        # Cheap keyword classifier — production deployments can swap
        # for an LLM call. The keyword path keeps recommendation cheap
        # since the calling LLM has already paid for the user
        # description; no need for another round-trip.
        desc = (args.get("description") or "").lower()
        candidates = [
            (t.get("match", {}).get("priority", 100), t)
            for t in TEMPLATES
            if t.get("match", {}).get("keywords")
        ]
        candidates.sort(key=lambda x: x[0])

        scored: list[tuple[int, int, dict[str, Any], list[str]]] = []
        for priority, tpl in candidates:
            matched_kws: list[str] = []
            for kw in tpl["match"]["keywords"]:
                # \b boundaries handle most natural language. For multi-
                # word keyword phrases ("share price", "cold call") we
                # still want substring — escape and check phrase form.
                pattern = r"\b" + re.escape(kw.lower()) + r"\b"
                if re.search(pattern, desc):
                    matched_kws.append(kw)
            if matched_kws:
                # Sort tuple is (-hits, priority, ...) so MORE hits beat
                # priority; on ties, lower priority (more specific) wins.
                scored.append((len(matched_kws), priority, tpl, matched_kws))

        if not scored:
            return {
                "template_id": "",
                "confidence": 0.0,
                "reasoning": (
                    "No template matched. The catalogue doesn't cover this "
                    "use case. Call create_custom_agent instead — ask the "
                    "user what skills they need and build a blank agent."
                ),
                "recommend_custom": True,
            }

        # Best = most hits, then lowest priority.
        scored.sort(key=lambda x: (-x[0], x[1]))
        best_hits, _best_priority, best_tpl, best_kws = scored[0]
        confidence = 0.85 if best_hits >= 2 else 0.4
        recommend_custom = best_hits < 2

        return {
            "template_id": best_tpl["id"],
            "name": best_tpl.get("name") or best_tpl["id"],
            "tagline": best_tpl.get("tagline") or "",
            "confidence": confidence,
            "reasoning": (
                f"matched {best_hits} keyword{'s' if best_hits != 1 else ''}: "
                + ", ".join(f"'{k}'" for k in best_kws)
            ),
            "recommend_custom": recommend_custom,
            # Surface up to 2 runners-up so the LLM can offer alternatives
            # without a second list_templates round-trip.
            "alternatives": [
                {"template_id": t["id"], "name": t.get("name") or t["id"], "hits": h}
                for h, _, t, _ in scored[1:3]
            ],
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


class CreateCustomAgentSkill(BaseSkill):
    """Build a blank agent without inheriting from any template.

    Use this when `recommend_template` returns `recommend_custom: true`
    OR `confidence < 0.5` — i.e. the catalogue doesn't cover the user's
    use case. The most common trigger today is "agent that searches the
    web for X" (no template ships with a web-search-first behaviour).
    The LLM should:
      1. Confirm the user wants a custom build (not a low-confidence
         template recommendation).
      2. Ask which skills they need — the prompt lists them. Map the
         user's phrasing ("web search" → `web_search`, "calculate" →
         `calculator`).
      3. Call this skill with a 2-4 word name + skill ids.
      4. Walk through greeting + system_prompt + voice via
         update_agent_field.
      5. Call publish_agent.

    Mirrors InstantiateTemplateSkill's draft-id stashing so the rest
    of the setup flow (update_agent_field / publish_agent) works
    unchanged after this call.
    """

    id = "create_custom_agent"
    display_name = "Create a custom agent"
    description = (
        "Create a blank draft agent when no template fits. Use this "
        "instead of instantiate_template if recommend_template returns "
        "low confidence or recommend_custom=true. Caller provides the "
        "name and a list of skill ids; system_prompt/greeting/voice "
        "can be set later via update_agent_field."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short concrete name, 2-4 words (e.g. 'Singapore news reader').",
            },
            "description": {
                "type": "string",
                "description": "One-line summary of what the agent does. Shown on the Agents card.",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of skill ids the agent can call. Must be valid "
                    "ids from the catalogue listed in the system prompt — "
                    "e.g. ['web_search', 'get_time']."
                ),
            },
            "system_prompt": {
                "type": "string",
                "description": (
                    "Behaviour instructions for the agent. Keep it focused "
                    "on the requested use case. Optional — can be set "
                    "later via update_agent_field."
                ),
            },
            "greeting": {
                "type": "string",
                "description": "The agent's first spoken line. Optional.",
            },
            "voice_language": {
                "type": "string",
                "description": "BCP-47 code like 'en-US'. Defaults to en-US.",
            },
        },
        "required": ["name", "skills"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.config import get_settings
        from openvox.db import db_session
        from openvox.db.models import Agent

        name = (args.get("name") or "").strip()
        if not name:
            return {"error": "name is required"}
        skills = args.get("skills") or []
        if not isinstance(skills, list) or not skills:
            return {"error": "skills must be a non-empty list of skill ids"}
        # Defensive: reject obviously-bogus skill ids. We don't fail
        # hard on unknown skills — the runtime will just no-op them —
        # but truncate to ≤10 to stop the LLM from stuffing in the
        # entire catalogue "just in case".
        skills = [str(s).strip() for s in skills if str(s).strip()][:10]

        settings = get_settings()
        payload = {
            "name": name,
            "description": (args.get("description") or "").strip(),
            "template_id": "",  # explicit: not from a template
            "system_prompt": (
                args.get("system_prompt")
                or "You are a helpful voice assistant. Keep responses under 2 sentences."
            ),
            "greeting": args.get("greeting") or f"Hi! I'm {name}. How can I help?",
            "skills": skills,
            "voice_id": settings.byteplus_tts_default_voice,
            "voice_language": args.get("voice_language") or "en-US",
            "llm_model": settings.byteplus_llm_model,
            "temperature": 0.7,
            "max_tokens": 800,
        }

        async with db_session() as s:
            a = Agent(**{k: v for k, v in payload.items() if hasattr(Agent, k)})
            s.add(a)
            await s.flush()
            agent_id = a.id

        await _set_draft_id(ctx, agent_id)
        logger.info(
            "setup-assistant: created custom draft agent %s name=%r skills=%s",
            agent_id, name, skills,
        )
        return {
            "agent_id": agent_id,
            "name": name,
            "skills": skills,
            "next": (
                "Optionally call update_agent_field for greeting / "
                "system_prompt / voice_id, then publish_agent when ready."
            ),
        }


SKILLS = [
    ListTemplatesSkill,
    RecommendTemplateSkill,
    InstantiateTemplateSkill,
    CreateCustomAgentSkill,
    UpdateAgentFieldSkill,
    PublishDraftSkill,
    DescribeRemainingSetupSkill,
]
