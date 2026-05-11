"""Skills for the voice-recording analysis template — transcribe an
uploaded audio URL (BytePlus AUC), then run sentiment + profanity on it."""

from __future__ import annotations

from typing import Any

from openvox.providers.base import LLMConfig, LLMMessage, ProviderType
from openvox.providers.registry import get_registry
from openvox.skills.base import BaseSkill, SkillContext


_PROFANITY_LEX = {
    # A small starter list. In production, plug a curated multilingual list
    # or a model like `unitary/toxic-bert`.
    "english": {"damn", "shit", "fuck", "bitch", "asshole", "bastard"},
}


class SentimentAnalyze(BaseSkill):
    id = "sentiment_analyze"
    display_name = "Sentiment analysis"
    description = (
        "Classify the overall sentiment of a text segment as positive / "
        "neutral / negative with a confidence score."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "empty text"}

        # Use whichever LLM is available — the agent's configured one
        # is the natural choice but we keep this skill self-contained.
        reg = get_registry()
        llm = reg.get("llm", "byteplus") or reg.get("llm", "openai") or reg.get("llm", "anthropic")
        if llm is None or not llm.is_available():
            # Fallback: trivial keyword heuristic.
            negs = sum(text.lower().count(w) for w in ("bad", "terrible", "awful", "hate"))
            poss = sum(text.lower().count(w) for w in ("good", "great", "love", "excellent"))
            label = "positive" if poss > negs else "negative" if negs > poss else "neutral"
            return {"label": label, "confidence": 0.5, "method": "heuristic"}

        prompt = (
            "Classify this text. Reply with EXACTLY one word: positive, neutral, or negative.\n\n"
            f"Text: {text}\n\nLabel:"
        )
        out = await llm.chat(
            [LLMMessage(role="user", content=prompt)],
            LLMConfig(model="", temperature=0.0, max_tokens=4, stream=False),
        )
        label = out.strip().lower().split()[0] if out else "neutral"
        if label not in ("positive", "negative", "neutral"):
            label = "neutral"
        return {"label": label, "confidence": 0.85, "method": "llm"}


class ProfanityCheck(BaseSkill):
    id = "profanity_check"
    display_name = "Profanity check"
    description = "Return the profane terms found in a text and a severity score (0-1)."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "language": {"type": "string", "default": "english"},
        },
        "required": ["text"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        text = (args.get("text") or "").lower()
        lang = args.get("language") or "english"
        lex = _PROFANITY_LEX.get(lang, _PROFANITY_LEX["english"])
        hits = sorted({w for w in lex if w in text})
        score = min(1.0, 0.25 * len(hits))
        return {"hits": hits, "severity": score, "language": lang}


class TranscribeRecording(BaseSkill):
    """Submit an audio file URL to BytePlus Seed ASR 2.0 (audio-file mode)
    and return the transcript text + per-utterance segments."""

    id = "transcribe_recording"
    display_name = "Transcribe a recording"
    description = (
        "Transcribe a hosted audio file (mp3/wav/ogg/m4a). "
        "The URL must be reachable from the OpenVox core service "
        "(use BytePlus TOS or an S3 presigned URL)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "audio_url": {"type": "string", "description": "Public/presigned URL"},
            "format": {"type": "string", "enum": ["mp3", "wav", "ogg", "m4a"], "default": "mp3"},
            "language": {"type": "string", "description": "BCP-47 tag, e.g. en-US"},
            "enable_speaker_info": {"type": "boolean", "default": False},
        },
        "required": ["audio_url"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        from openvox.providers.byteplus.stt import BytePlusSTT  # local import

        url = (args.get("audio_url") or "").strip()
        if not url:
            return {"error": "audio_url is required"}

        # Use the registry instance so we share warm settings with the
        # streaming pipeline.
        stt = get_registry().get(ProviderType.STT, "byteplus")
        if not isinstance(stt, BytePlusSTT) or not stt.is_available():
            return {"error": "BytePlus voice provider not configured"}

        result = await stt.transcribe_file_url(
            url,
            language=args.get("language"),
            format=args.get("format") or "mp3",
            enable_speaker_info=bool(args.get("enable_speaker_info", False)),
        )
        text = ((result.get("result") or {}).get("text")) or ""
        utterances = ((result.get("result") or {}).get("utterances")) or []
        return {
            "text": text,
            "utterances": [
                {
                    "start_ms": u.get("start_time"),
                    "end_ms": u.get("end_time"),
                    "text": u.get("text"),
                    "speaker": (u.get("additions") or {}).get("speaker"),
                }
                for u in utterances
            ],
            "duration_ms": (result.get("audio_info") or {}).get("duration"),
        }


SKILLS = [SentimentAnalyze, ProfanityCheck, TranscribeRecording]
