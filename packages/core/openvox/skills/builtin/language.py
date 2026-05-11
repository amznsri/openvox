"""Skills for the Multilingual Customer Support IVR template.

Language detection happens two ways:

  * BytePlus Seed ASR's batch (`bigmodel_nostream`) mode supports
    `enable_auto_lang` server-side; the orchestrator stashes the result
    on ctx.metadata as `last_language`.
  * Streaming mode (`bigmodel_async`) doesn't support auto-detect, so
    the `detect_language` skill falls back to **LLM classification** on
    the latest transcript — works regardless of STT mode.

Skills:
  * `detect_language`     — classify a phrase's language (BCP-47).
  * `route_to_specialist` — pick a specialist queue from (topic, lang).
"""

from __future__ import annotations

from typing import Any

from openvox.providers.base import LLMConfig, LLMMessage, ProviderType
from openvox.providers.registry import get_registry
from openvox.skills.base import BaseSkill, SkillContext


# Demo routing table (topic, language) → specialist queue.
# Falls back to billing-en if no match.
_QUEUES = {
    ("billing", "en"): {"queue": "billing-en", "wait_min": 3, "agent_name": "Maya"},
    ("billing", "zh"): {"queue": "billing-zh", "wait_min": 2, "agent_name": "李伟"},
    ("billing", "es"): {"queue": "billing-es", "wait_min": 4, "agent_name": "Carlos"},
    ("technical", "en"): {"queue": "tech-en", "wait_min": 5, "agent_name": "Diego"},
    ("technical", "zh"): {"queue": "tech-zh", "wait_min": 4, "agent_name": "王芳"},
    ("technical", "es"): {"queue": "tech-es", "wait_min": 6, "agent_name": "Lucia"},
    ("sales", "en"): {"queue": "sales-en", "wait_min": 1, "agent_name": "Priya"},
    ("sales", "zh"): {"queue": "sales-zh", "wait_min": 1, "agent_name": "张敏"},
    ("sales", "es"): {"queue": "sales-es", "wait_min": 1, "agent_name": "Elena"},
}


def _lang_short(language: str) -> str:
    """Map a BCP-47 tag to its primary subtag — `en-US` → `en`."""
    return (language or "").split("-")[0].lower() or "en"


class DetectLanguage(BaseSkill):
    id = "detect_language"
    display_name = "Detect spoken language"
    description = (
        "Identify the BCP-47 language tag for a phrase or for the last user "
        "utterance. Pass `text` to classify a specific phrase; omit it to "
        "use whatever STT detected for the last turn. Returns "
        "`language` (e.g. 'es-MX'), `language_short` ('es'), and `confidence`."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Optional — phrase to classify. If empty, uses the last STT result.",
            }
        },
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        text = (args.get("text") or "").strip()
        meta = ctx.metadata or {}

        # If caller didn't supply text and ASR already attached a language,
        # echo that — fastest path.
        if not text and meta.get("last_language"):
            full = meta["last_language"]
            return {
                "language": full,
                "language_short": _lang_short(full),
                "confidence": meta.get("last_language_confidence", 1.0),
                "method": "asr",
            }

        # No text and no prior detection → safe default.
        if not text:
            return {
                "language": "en-US",
                "language_short": "en",
                "confidence": 0.5,
                "method": "default",
            }

        # LLM-based classification — works regardless of STT mode.
        llm = (
            get_registry().get(ProviderType.LLM, "byteplus")
            or get_registry().get(ProviderType.LLM, "openai")
            or get_registry().get(ProviderType.LLM, "anthropic")
        )
        if llm is None or not llm.is_available():
            return {
                "language": "en-US",
                "language_short": "en",
                "confidence": 0.3,
                "method": "fallback",
                "warning": "no LLM available for classification",
            }
        prompt = (
            "Identify the language of this phrase. Reply with EXACTLY one "
            "BCP-47 tag like 'en-US', 'zh-CN', 'es-MX', 'fr-FR' — no other words.\n\n"
            f"Phrase: {text!r}\n\nLanguage:"
        )
        out = await llm.chat(
            [LLMMessage(role="user", content=prompt)],
            LLMConfig(model="", temperature=0.0, max_tokens=8, stream=False),
        )
        tag = (out or "en-US").strip().split()[0].strip(".,;:'\"")
        return {
            "language": tag,
            "language_short": _lang_short(tag),
            "confidence": 0.9,
            "method": "llm",
        }


class RouteToSpecialist(BaseSkill):
    id = "route_to_specialist"
    display_name = "Route to specialist queue"
    description = (
        "Pick a specialist queue based on topic + caller language. Returns "
        "the queue name, estimated wait minutes, and the live agent's first "
        "name so the IVR can hand off gracefully."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["billing", "technical", "sales", "other"],
                "description": "Topic of the call.",
            },
            "language": {
                "type": "string",
                "description": "BCP-47 tag, e.g. 'en-US', 'zh-CN'. Defaults to detected language.",
            },
        },
        "required": ["topic"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        topic = (args.get("topic") or "other").lower()
        lang = args.get("language") or (ctx.metadata or {}).get("last_language") or "en-US"
        short = _lang_short(lang)
        key = (topic, short)
        queue = _QUEUES.get(key) or _QUEUES.get(("billing", "en"))
        return {"topic": topic, "language": lang, "language_short": short, "queue": queue}


SKILLS = [DetectLanguage, RouteToSpecialist]
