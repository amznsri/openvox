"""Job execution dispatcher.

Three kinds today; each grows independently. The dispatcher records a
`JobRun` row with the result/error, and updates `ScheduledJob.last_*`
so the dashboard can show recent status.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openvox.db import db_session
from openvox.db.models import JobRun, ScheduledJob

logger = logging.getLogger(__name__)


async def execute_job(job_id: str) -> None:
    """Top-level entry — called by APScheduler. Wraps the kind dispatch
    in a `JobRun` row so we always have a history record (even on crash)."""
    started = datetime.now(timezone.utc)
    run_id: str | None = None
    try:
        async with db_session() as s:
            job = await s.get(ScheduledJob, job_id)
            if job is None:
                logger.warning("job %s vanished before execution", job_id)
                return
            job.last_run_at = started
            job.last_status = "running"
            run = JobRun(job_id=job_id, started_at=started, status="running")
            s.add(run)
            await s.flush()
            run_id = run.id
            kind = job.kind
            payload = dict(job.payload or {})
            agent_id = job.agent_id

        result, error = await _dispatch(kind, payload, agent_id)
        status = "error" if error else "success"
        async with db_session() as s:
            if run_id is not None:
                run = await s.get(JobRun, run_id)
                if run is not None:
                    run.ended_at = datetime.now(timezone.utc)
                    run.status = status
                    run.result = result or {}
                    run.error = error or ""
            job = await s.get(ScheduledJob, job_id)
            if job is not None:
                job.last_status = status
                job.last_error = error or ""

    except Exception as e:
        logger.exception("job %s crashed", job_id)
        async with db_session() as s:
            if run_id is not None:
                run = await s.get(JobRun, run_id)
                if run is not None:
                    run.ended_at = datetime.now(timezone.utc)
                    run.status = "error"
                    run.error = str(e)
            job = await s.get(ScheduledJob, job_id)
            if job is not None:
                job.last_status = "error"
                job.last_error = str(e)


async def _dispatch(kind: str, payload: dict[str, Any], agent_id: str) -> tuple[dict[str, Any], str]:
    if kind == "agent_query":
        return await _run_agent_query(agent_id, payload)
    if kind == "skill_run":
        return await _run_skill(agent_id, payload)
    if kind == "audio_batch":
        return await _run_audio_batch(agent_id, payload)
    if kind == "outbound_call_batch":
        return await _run_outbound_call_batch(agent_id, payload)
    return {}, f"unknown job kind: {kind!r}"


# ── Kind: agent_query ────────────────────────────────────────────
# Payload: {"prompt": "summarise today's recordings"}
# Runs an LLM call against the agent's configured model + system prompt.


async def _run_agent_query(agent_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from openvox.db.models import Agent
    from openvox.providers import ProviderType, get_registry
    from openvox.providers.base import LLMConfig, LLMMessage, LLMProvider

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return {}, "payload.prompt required"
    if not agent_id:
        return {}, "agent_id required for agent_query"

    async with db_session() as s:
        agent = await s.get(Agent, agent_id)
        if agent is None:
            return {}, f"agent {agent_id} not found"
        system = agent.system_prompt
        model = agent.llm_model
        temperature = agent.temperature
        max_tokens = agent.max_tokens
        provider_id = agent.llm_provider

    llm = get_registry().get(ProviderType.LLM, provider_id)
    if llm is None or not isinstance(llm, LLMProvider) or not llm.is_available():
        return {}, f"LLM provider {provider_id!r} unavailable"

    messages = [LLMMessage(role="system", content=system), LLMMessage(role="user", content=prompt)]
    cfg = LLMConfig(model=model, temperature=temperature, max_tokens=max_tokens, stream=False)
    answer = await llm.chat(messages, cfg)
    return {"prompt": prompt, "answer": answer}, ""


# ── Kind: skill_run ──────────────────────────────────────────────
# Payload: {"skill_id": "lookup_order", "args": {"order_id": "ORD-1001"}}
# Direct skill invocation — no LLM in the loop.


async def _run_skill(agent_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from openvox.skills import SkillContext
    from openvox.skills.runner import SkillRunner

    skill_id = (payload.get("skill_id") or "").strip()
    args = payload.get("args") or {}
    if not skill_id:
        return {}, "payload.skill_id required"

    runner = SkillRunner(
        skill_ids=[skill_id],
        ctx=SkillContext(agent_id=agent_id, metadata={"source": "scheduler"}),
    )
    result = await runner.invoke(skill_id, args)
    if not result.get("ok"):
        return result, result.get("error") or "skill failed"
    return result, ""


# ── Kind: audio_batch ────────────────────────────────────────────
# Payload: {"folder": "/data/recordings", "glob": "*.mp3"}
# Walks a folder, runs the same pipeline as /playground/audio_analyze
# on each unprocessed file. Skips files seen by previous runs.


async def _run_audio_batch(agent_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from openvox.api.routes.playground import _decode_to_pcm16k, _stream_pcm_to_stt
    from openvox.providers import ProviderType, get_registry
    from openvox.providers.base import STTProvider
    from openvox.skills import SkillContext
    from openvox.skills.runner import SkillRunner

    folder = payload.get("folder") or "/data/recordings"
    glob = payload.get("glob") or "*"
    language = payload.get("language") or "en-US"
    state_file = payload.get("state_file") or os.path.join(folder, ".openvox_processed")

    p = Path(folder)
    if not p.is_dir():
        return {"folder": folder}, f"folder does not exist: {folder}"

    # Load previously processed set so subsequent runs only see new files.
    processed: set[str] = set()
    sf = Path(state_file)
    if sf.exists():
        try:
            processed = set(sf.read_text().splitlines())
        except Exception:
            pass

    stt = get_registry().get(ProviderType.STT, "byteplus")
    if not isinstance(stt, STTProvider) or not stt.is_available():
        return {}, "BytePlus STT unavailable"

    runner = SkillRunner(
        skill_ids=["sentiment_analyze", "profanity_check"],
        ctx=SkillContext(agent_id=agent_id, metadata={"source": "scheduler"}),
    )

    results: list[dict[str, Any]] = []
    newly_processed: list[str] = []

    for f in sorted(p.glob(glob)):
        if not f.is_file():
            continue
        rel = str(f.resolve())
        if rel in processed:
            continue
        try:
            data = f.read_bytes()
            pcm, duration_ms = await asyncio.to_thread(_decode_to_pcm16k, data, None, f.name)
            transcript, _ = await _stream_pcm_to_stt(pcm, duration_ms, stt, language=language)
            entry: dict[str, Any] = {"file": rel, "duration_ms": duration_ms, "transcript": transcript}
            if transcript:
                entry["sentiment"] = await runner.invoke("sentiment_analyze", {"text": transcript})
                entry["profanity"] = await runner.invoke("profanity_check", {"text": transcript})
            results.append(entry)
            newly_processed.append(rel)
        except Exception as e:
            logger.exception("audio_batch failed for %s", rel)
            results.append({"file": rel, "error": str(e)})

    # Persist the processed marker so the next run picks up only new files.
    if newly_processed:
        try:
            sf.parent.mkdir(parents=True, exist_ok=True)
            with sf.open("a") as out:
                for r in newly_processed:
                    out.write(r + "\n")
        except Exception as e:
            logger.warning("could not write state file %s: %s", state_file, e)

    return {"folder": folder, "processed": len(newly_processed), "items": results}, ""


# ── Kind: outbound_call_batch ────────────────────────────────────
# Payload: {
#   "to_numbers":   ["+14155550101", "+14155550102"],   # ← explicit list, OR
#   "from_skill":   "fetch_next_lead",                  #   ← pull from a skill iteratively
#   "max_calls":    5,
#   "callback_url": "https://your-ngrok.example/api/v1/telephony/twilio/voice",
#   "preview":      true                                # safety: dry-run by default
# }
# Initiates up to `max_calls` outbound Twilio calls against `agent_id`.
# If preview=true (default), no calls are placed — just returns who *would*
# have been called. Flip preview=false to actually dial.


async def _run_outbound_call_batch(agent_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not agent_id:
        return {}, "agent_id required for outbound_call_batch"

    to_numbers = list(payload.get("to_numbers") or [])
    from_skill = (payload.get("from_skill") or "").strip()
    max_calls = int(payload.get("max_calls") or 5)
    callback_url = (payload.get("callback_url") or "").strip()
    preview = bool(payload.get("preview", True))

    # If the caller didn't pass an explicit number list, pull from a skill
    # (typically `fetch_next_lead`) up to max_calls times.
    lead_meta: list[dict[str, Any]] = []
    if not to_numbers and from_skill:
        from openvox.skills import SkillContext
        from openvox.skills.runner import SkillRunner
        runner = SkillRunner(
            skill_ids=[from_skill],
            ctx=SkillContext(agent_id=agent_id, metadata={"source": "scheduler"}),
        )
        for _ in range(max_calls):
            res = await runner.invoke(from_skill, {})
            lead = ((res or {}).get("output") or {}).get("lead")
            if not lead or not lead.get("phone"):
                break
            to_numbers.append(lead["phone"])
            lead_meta.append({"id": lead.get("id"), "company": lead.get("company")})

    to_numbers = to_numbers[:max_calls]
    if not to_numbers:
        return {"calls": [], "preview": preview, "message": "no numbers to dial"}, ""

    if preview or not callback_url:
        return {
            "preview": True,
            "would_call": to_numbers,
            "leads": lead_meta,
            "message": (
                "Preview mode — no calls placed. Set payload.preview=false "
                "and provide a publicly-reachable callback_url to dial for real."
            ),
        }, ""

    # Real dial-out.
    from openvox.telephony import place_call
    placed: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, number in enumerate(to_numbers):
        meta = lead_meta[i] if i < len(lead_meta) else {}
        try:
            res = await place_call(
                to=number,
                agent_id=agent_id,
                callback_url=callback_url,
                lead_id=meta.get("id"),
            )
            placed.append(
                {"to": number, "sid": res.get("sid"), "status": res.get("status"), "lead": meta}
            )
        except Exception as e:
            logger.exception("outbound dial failed for %s", number)
            errors.append(f"{number}: {e}")

    return {"calls": placed, "errors": errors, "preview": False}, ""
