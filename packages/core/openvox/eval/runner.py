"""Eval execution runners — replay + persona sparring.

Two entry points the API layer (`routes/evals.py`) calls into:

  run_replay_eval(agent, recording, criteria)
      Feed each user turn from `recording.transcript` into the agent
      (as if it were a freshly-transcribed STT-final), capture the
      agent's responses, judge the result against criteria.

  run_persona_eval(candidate_agent, persona, criteria, max_turns)
      Run two LLMs against each other: persona drives "user" turns,
      candidate agent drives "assistant" turns. Stop when either side
      says they're done or when max_turns is hit. Judge the result.

Both return an EvalRun row ready to persist.

Why we use plain LLM calls here, not full VoiceSession:
    Audio is irrelevant for eval correctness — what matters is the
    *content* of the responses. Running LLM-only is ~10× faster than
    spinning up TTS / STT, doesn't burn voice-API quota, and gives
    us deterministic re-runs of the same recording.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from openvox.eval.judge import judge_transcript
from openvox.providers import ProviderType, get_registry
from openvox.providers.base import LLMConfig, LLMMessage, LLMProvider
from openvox.skills.runner import SkillRunner
from openvox.skills import SkillContext

logger = logging.getLogger(__name__)


# ── Replay: recording → candidate agent ─────────────────────────────


async def run_replay_eval(
    *,
    agent: dict,                # Agent dict (system_prompt, llm_provider, llm_model, skills, ...)
    recording_transcript: list[dict],
    criteria: list[str],
) -> dict[str, Any]:
    """Replay the user turns from `recording_transcript` against `agent`.

    Returns: {transcript, verdict, score, judge_breakdown, turn_count,
              duration_ms, error}
    """
    started = datetime.now(timezone.utc)
    llm = get_registry().get(ProviderType.LLM, agent.get("llm_provider", "byteplus"))
    if llm is None or not isinstance(llm, LLMProvider) or not llm.is_available():
        return _error_result("candidate LLM unavailable", started)

    history: list[LLMMessage] = [
        LLMMessage(role="system", content=agent.get("system_prompt") or "")
    ]
    new_transcript: list[dict] = []
    runner = SkillRunner(
        skill_ids=list(agent.get("skills") or []),
        ctx=SkillContext(agent_id=agent.get("id", ""), metadata={"source": "eval-replay"}),
    )

    cfg = LLMConfig(
        model=agent.get("llm_model") or "",
        temperature=agent.get("temperature", 0.7),
        max_tokens=int(agent.get("max_tokens") or 600),
        stream=False,
        tools=runner.tool_specs() or None,
    )

    for turn in recording_transcript:
        role = turn.get("role", "")
        text = (turn.get("text") or "").strip()
        if role != "user" or not text:
            continue
        history.append(LLMMessage(role="user", content=text))
        new_transcript.append({"role": "user", "text": text})
        try:
            reply = await llm.chat(history, cfg)
        except Exception as e:
            logger.exception("replay candidate LLM error")
            return _error_result(f"candidate LLM error: {e}", started, new_transcript)
        history.append(LLMMessage(role="assistant", content=reply))
        new_transcript.append({"role": "assistant", "text": reply})

    verdict = await judge_transcript(
        transcript=new_transcript,
        criteria=criteria,
    )
    ended = datetime.now(timezone.utc)
    return {
        "transcript": new_transcript,
        "verdict": verdict.get("verdict", ""),
        "score": verdict.get("score", 0.0),
        "judge_breakdown": verdict.get("breakdown", []),
        "turn_count": sum(1 for t in new_transcript if t["role"] == "user"),
        "duration_ms": int((ended - started).total_seconds() * 1000),
        "error": verdict.get("error", ""),
        "started_at": started,
        "ended_at": ended,
    }


# ── Persona sparring: persona ↔ candidate agent ─────────────────────


async def run_persona_eval(
    *,
    agent: dict,
    persona: dict,
    criteria: list[str],
    max_turns: int = 8,
) -> dict[str, Any]:
    """Two LLMs in conversation: persona drives user turns, agent
    responds. Hard turn cap so costs don't balloon."""
    started = datetime.now(timezone.utc)

    cand_llm = get_registry().get(ProviderType.LLM, agent.get("llm_provider", "byteplus"))
    persona_llm = get_registry().get(
        ProviderType.LLM, persona.get("llm_provider") or "byteplus"
    )
    if (
        cand_llm is None or persona_llm is None
        or not isinstance(cand_llm, LLMProvider) or not isinstance(persona_llm, LLMProvider)
        or not cand_llm.is_available() or not persona_llm.is_available()
    ):
        return _error_result("persona/candidate LLM unavailable", started)

    # Persona maintains its own history (with greeting opener) so it
    # has continuity. Candidate also has its own — they only share
    # the surface conversation.
    persona_history: list[LLMMessage] = [
        LLMMessage(role="system", content=persona.get("system_prompt") or ""),
        LLMMessage(role="user", content="Start the call. Say your first line."),
    ]
    cand_history: list[LLMMessage] = [
        LLMMessage(role="system", content=agent.get("system_prompt") or "")
    ]
    transcript: list[dict] = []

    cand_cfg = LLMConfig(
        model=agent.get("llm_model") or "",
        temperature=agent.get("temperature", 0.7),
        max_tokens=400, stream=False,
    )
    persona_cfg = LLMConfig(
        model=persona.get("llm_model") or "",
        temperature=0.8,  # personas need variety
        max_tokens=200, stream=False,
    )

    try:
        for turn in range(max_turns):
            # Persona speaks first.
            user_msg = (await persona_llm.chat(persona_history, persona_cfg)).strip()
            if not user_msg:
                break
            transcript.append({"role": "user", "text": user_msg})
            persona_history.append(LLMMessage(role="assistant", content=user_msg))
            cand_history.append(LLMMessage(role="user", content=user_msg))

            # Candidate responds.
            reply = (await cand_llm.chat(cand_history, cand_cfg)).strip()
            transcript.append({"role": "assistant", "text": reply})
            cand_history.append(LLMMessage(role="assistant", content=reply))
            persona_history.append(LLMMessage(role="user", content=reply))

            # Cheap heuristic: if either side announces the call is
            # over, stop the loop.
            low = (user_msg + " " + reply).lower()
            if any(p in low for p in ("goodbye", "have to go", "thanks for calling", "have a great day")):
                break
    except Exception as e:
        logger.exception("persona eval LLM error")
        return _error_result(f"persona LLM error: {e}", started, transcript)

    verdict = await judge_transcript(transcript=transcript, criteria=criteria)
    ended = datetime.now(timezone.utc)
    return {
        "transcript": transcript,
        "verdict": verdict.get("verdict", ""),
        "score": verdict.get("score", 0.0),
        "judge_breakdown": verdict.get("breakdown", []),
        "turn_count": sum(1 for t in transcript if t["role"] == "user"),
        "duration_ms": int((ended - started).total_seconds() * 1000),
        "error": verdict.get("error", ""),
        "started_at": started,
        "ended_at": ended,
    }


def _error_result(msg: str, started: datetime, transcript: list[dict] | None = None) -> dict[str, Any]:
    ended = datetime.now(timezone.utc)
    return {
        "transcript": transcript or [],
        "verdict": "error",
        "score": 0.0,
        "judge_breakdown": [],
        "turn_count": 0,
        "duration_ms": int((ended - started).total_seconds() * 1000),
        "error": msg,
        "started_at": started,
        "ended_at": ended,
    }
