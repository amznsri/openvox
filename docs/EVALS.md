# OpenVox evaluation framework

Voice agents drift. The prompt that worked yesterday produces awkward
calls today because you swapped the LLM, added a skill, or changed a
voice. None of the popular voice-agent platforms ship a real way to
catch this — they leave it to "you'll notice in production". OpenVox
ships a first-class eval framework instead.

## Three pieces

| Piece | What it is | Why it exists |
|---|---|---|
| **Recordings** | Saved snapshots of real conversation transcripts. | Replay yesterday's customer call against today's agent config — does it still resolve correctly? |
| **Personas** | Synthetic "user" agents (angry customer, confused elder, ESL speaker, …). | Spar against your agent without burning real customers. 5 built-in, easy to add more. |
| **EvalRuns** | One execution: (recording \| persona) × candidate agent + criteria → verdict + breakdown. | Persistent record so the dashboard / CI can compare runs over time. |

Each piece is a normal SQL table (`recordings`, `personas`,
`eval_runs`); the API lives under `/api/v1/evals/*`.

## The judge

We don't trust an LLM to score itself. Instead we use a separate
**LLM-as-judge** prompt that evaluates the resulting transcript
against user-supplied criteria. Each criterion is evaluated
**independently**, so partial passes are honest:

```jsonc
{
  "criteria": [
    "Did the agent collect the order number?",
    "Did the agent stay polite throughout?",
    "Did the agent escalate when the user asked for a human?"
  ]
}
```

The judge returns a per-criterion verdict (`pass | partial | fail`)
plus a one-sentence reasoning quote. The Python aggregator turns that
into:

- **score**: fraction of criteria that passed (partials = 0.5 each).
- **verdict**: `pass` if all pass, `fail` if none pass, `partial` otherwise.

Aggregation is deterministic — only the per-criterion judgement is
LLM-driven. Same transcript + same criteria + same judge model →
identical score every time.

## Running an eval — the three ways

### 1. From the dashboard

(Coming in Session 8 dashboard pass) — visit `/dashboard/evals`,
pick an agent, pick a recording or persona, optional criteria, **Run**.
Result lands in the table with a pass/fail badge.

### 2. From curl

```bash
# Persona sparring — spin up a 5-built-in persona vs your agent
curl -X POST http://localhost:3001/api/v1/evals/run \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "069bfeea-dec3-414f-980d-6ac98adc632d",
    "persona_id": "angry-customer-en",
    "criteria": [
      "Did the agent offer a clear resolution path?",
      "Did the agent stay polite throughout?"
    ],
    "max_turns": 6
  }'
```

Response includes the full transcript, the judge's per-criterion
breakdown, score, and verdict.

```bash
# Replay — first promote a real session into a Recording, then re-run
SESSION_ID="..."  # any session id from /api/v1/sessions
RECORDING=$(curl -X POST http://localhost:3001/api/v1/evals/recordings/from-session \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\": \"$SESSION_ID\", \"name\": \"baseline-2026-05-14\"}" \
  | jq -r .id)

curl -X POST http://localhost:3001/api/v1/evals/run \
  -H 'Content-Type: application/json' \
  -d "{\"agent_id\": \"...\", \"recording_id\": \"$RECORDING\", \"criteria\": [\"...\"]}"
```

### 3. From CI

See `.github/workflows/evals.example.yml`. The workflow:

1. Triggers on every PR that touches `packages/core/**` or
   `apps/dashboard/**`.
2. Hits your **OpenVox base URL** (set via `OPENVOX_BASE_URL`
   secret — ngrok / staging / prod).
3. Runs each `(agent, persona)` pair from the matrix.
4. **Fails the PR** if any verdict is `fail` or `error`.

Drop it into `.github/workflows/`, set the secret, edit the agent /
persona IDs, and your PRs gain a real regression gate.

## Built-in personas

| ID | Profile |
|---|---|
| `angry-customer-en` | Frustrated, wants a refund + shipping refund, escalates to supervisor if stalled. |
| `confused-elder-en` | 78 years old, struggles with jargon, gets distracted. |
| `non-native-speaker-en` | English-as-second-language, occasional Spanish code-switch. |
| `in-a-hurry-en` | 30 seconds to talk, ends the call if the agent rambles. |
| `security-paranoid-en` | Suspicious it's a scam, demands verification before sharing details. |

Add your own with `POST /api/v1/evals/personas`. Built-ins are seeded
on every startup; user-created personas are left alone.

## What this doesn't do (yet)

- **Audio-fidelity evals.** We replay user turns as text only — TTS
  quality / voice cloning isn't part of the verdict. Add when there's
  a user asking for it.
- **Multi-judge aggregation.** Single judge LLM today. Could use
  multiple judges with weighted voting for higher confidence.
- **Trace-based replay.** Replay re-runs the LLM from scratch; it
  doesn't replay the original LLM trace, so non-determinism from
  temperature shows up. Set `temperature=0` on critical agents for
  stable scores.
- **Persona memory across runs.** Each run starts the persona fresh.

If you hit one of these, file an issue.
