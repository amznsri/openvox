"""Skills — extension framework for tool-use / function-calling.

A *Skill* is a Python class that implements `BaseSkill` and exposes:

  - `id` (string), `display_name`, `description`
  - `parameters` — JSON schema describing the function-call args
  - async `run(args: dict) -> Any`

Skills can be:
  1. **Built-in** — shipped in `openvox.skills.builtin`.
  2. **Local** — dropped into `~/.openvox/skills/<name>.py` and auto-discovered.
  3. **Pip-installed** — registered via the `openvox.skills` entry-point group.
  4. **Git-pulled** — `openvox skills install <git-url>` clones into the
     local skills directory.

Skills declare optional config via Pydantic models so the dashboard can
render a settings form. They run in-process with a per-call timeout.
"""

from openvox.skills.base import BaseSkill, SkillContext, SkillResult, skill
from openvox.skills.registry import SkillRegistry, get_skill_registry
from openvox.skills.runner import SkillRunner

__all__ = [
    "BaseSkill",
    "SkillContext",
    "SkillRegistry",
    "SkillResult",
    "SkillRunner",
    "get_skill_registry",
    "skill",
]
