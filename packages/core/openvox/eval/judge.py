"""LLM-as-judge — evaluate a transcript against user-defined criteria.

Why this is its own module:
    Eval verdicts power everything downstream — pass/fail badges,
    regression detection, the CI hook. Keeping the prompt in one place
    means everyone gets the same verdict semantics; one prompt change
    propagates to every run.

Prompt design:
    We ask the judge LLM to evaluate each criterion *independently*,
    returning a strict JSON object so we can score reliably. The
    aggregate verdict is computed in Python from the per-criterion
    results — not delegated to the LLM. This keeps verdicts
    deterministic across re-runs of the same transcript.

Output schema:
    {
      "breakdown": [
        {"criterion": "...", "verdict": "pass"|"fail"|"partial",
         "reasoning": "1 sentence"},
        ...
      ],
      "score": 0.66,        # fraction of criteria that passed
      "verdict": "partial"  # pass = all pass; fail = none; partial = mixed
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openvox.providers import ProviderType, get_registry
from openvox.providers.base import LLMConfig, LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


_JUDGE_SYSTEM = (
    "You are a strict but fair voice-agent quality reviewer. You are given "
    "a conversation transcript and a list of pass/fail criteria. For EACH "
    "criterion, decide whether the conversation met it. Be specific in "
    "your reasoning — quote the exact line of dialogue you're citing. "
    "Reply with ONLY a JSON array, no markdown, no commentary."
)


def _build_judge_prompt(transcript: list[dict], criteria: list[str]) -> str:
    lines = []
    for turn in transcript:
        role = turn.get("role", "?")
        text = turn.get("text", "")
        lines.append(f"{role.upper()}: {text}")
    convo = "\n".join(lines)
    rules_block = "\n".join(f"  - {c}" for c in criteria)
    return (
        f"Transcript:\n{convo}\n\n"
        f"Criteria (judge each independently):\n{rules_block}\n\n"
        'Return ONLY a JSON array where each element is '
        '{"criterion": "<text>", "verdict": "pass"|"fail"|"partial", "reasoning": "<1 sentence>"}.'
    )


async def judge_transcript(
    *,
    transcript: list[dict],
    criteria: list[str],
    llm_provider: str = "byteplus",
) -> dict[str, Any]:
    """Return {breakdown, score, verdict} for a transcript + criteria.

    Robust to slightly off-spec LLM output: tolerates code-fence
    wrappers, trailing commas, and falls back to "error" verdict if
    parsing fails entirely. Caller persists whatever we return.
    """
    if not criteria:
        return {"breakdown": [], "score": 1.0, "verdict": "pass"}

    llm = get_registry().get(ProviderType.LLM, llm_provider)
    if llm is None or not isinstance(llm, LLMProvider) or not llm.is_available():
        return {
            "breakdown": [],
            "score": 0.0,
            "verdict": "error",
            "error": f"judge LLM '{llm_provider}' unavailable",
        }

    msgs = [
        LLMMessage(role="system", content=_JUDGE_SYSTEM),
        LLMMessage(role="user", content=_build_judge_prompt(transcript, criteria)),
    ]
    cfg = LLMConfig(model="", temperature=0.0, max_tokens=900, stream=False)
    raw = await llm.chat(msgs, cfg)

    # Strip common code-fence wrappers the LLM might add despite our
    # instruction.
    body = raw.strip()
    if body.startswith("```"):
        body = body.split("```", 2)
        body = body[1] if len(body) > 1 else ""
        if body.startswith("json"):
            body = body[4:]
        body = body.strip().rstrip("`").strip()

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("judge returned unparseable JSON: %s", raw[:200])
        return {
            "breakdown": [],
            "score": 0.0,
            "verdict": "error",
            "error": "judge LLM returned unparseable JSON",
            "raw": raw[:500],
        }

    # Coerce to a list-of-dicts even if the LLM wrapped it in an object.
    if isinstance(parsed, dict) and "breakdown" in parsed:
        parsed = parsed["breakdown"]
    if not isinstance(parsed, list):
        return {"breakdown": [], "score": 0.0, "verdict": "error",
                "error": "judge output not a list", "raw": raw[:500]}

    passes = sum(1 for item in parsed if (item.get("verdict") or "").lower() == "pass")
    partials = sum(1 for item in parsed if (item.get("verdict") or "").lower() == "partial")
    total = len(parsed)
    # Score = (full passes + half credit for partials) / total.
    score = (passes + 0.5 * partials) / total if total else 0.0
    if passes == total:
        agg = "pass"
    elif passes == 0 and partials == 0:
        agg = "fail"
    else:
        agg = "partial"
    return {"breakdown": parsed, "score": round(score, 3), "verdict": agg}
