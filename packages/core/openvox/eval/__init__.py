"""Voice-agent evaluation framework — replay + persona sparring + LLM judge.

Three pieces:
  - personas.py: built-in personas seeded into the DB on startup.
  - runner.py:   replay (recorded transcript → re-run against new agent)
                 and persona-vs-agent sparring runners.
  - judge.py:    LLM-as-judge that takes a transcript + criteria and
                 returns a pass/fail verdict with reasoning.

The user-facing API lives at /api/v1/evals — see routes/evals.py.
"""

from openvox.eval.runner import run_persona_eval, run_replay_eval

__all__ = ["run_persona_eval", "run_replay_eval"]
