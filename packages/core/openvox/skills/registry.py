"""Skill registry — discovers built-ins, local files, entry-points."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
from pathlib import Path
from threading import RLock

from openvox.config import get_settings
from openvox.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._classes: dict[str, type[BaseSkill]] = {}
        self._instances: dict[str, BaseSkill] = {}
        self._discovered = False

    def register(self, cls: type[BaseSkill]) -> None:
        if not getattr(cls, "id", None):
            raise ValueError(f"{cls.__name__} must set `id`")
        with self._lock:
            self._classes[cls.id] = cls

    def get(self, sid: str) -> BaseSkill | None:
        with self._lock:
            inst = self._instances.get(sid)
            if inst is not None:
                return inst
            cls = self._classes.get(sid)
            if cls is None:
                return None
            inst = cls()
            self._instances[sid] = inst
            return inst

    def list(self) -> list[dict]:
        out = []
        with self._lock:
            for sid, cls in self._classes.items():
                out.append(
                    {
                        "id": sid,
                        "display_name": getattr(cls, "display_name", sid),
                        "description": getattr(cls, "description", ""),
                        "parameters": getattr(cls, "parameters", {}),
                        "config_schema": getattr(cls, "config_schema", {}),
                    }
                )
        return sorted(out, key=lambda x: x["id"])

    def discover(self) -> None:
        """Discover built-ins, entry-points, and local-folder skills."""
        if self._discovered:
            return
        self._load_builtins()
        self._load_entrypoints()
        self._load_local_folder()
        self._discovered = True

    # ── private ──────────────────────────────────────────────────────
    def _load_builtins(self) -> None:
        from openvox.skills import builtin as _builtin

        # Each module under builtin should export `SKILLS = [Cls1, Cls2, ...]`
        for mod_name in getattr(_builtin, "__all__", []):
            try:
                mod = importlib.import_module(f"openvox.skills.builtin.{mod_name}")
                for cls in getattr(mod, "SKILLS", []):
                    self.register(cls)
            except Exception as e:  # pragma: no cover
                logger.warning("failed to load built-in skill module %s: %s", mod_name, e)

    def _load_entrypoints(self) -> None:
        try:
            eps = importlib.metadata.entry_points(group="openvox.skills")
        except Exception:
            return
        for ep in eps:
            try:
                cls = ep.load()
                if isinstance(cls, type) and issubclass(cls, BaseSkill):
                    self.register(cls)
            except Exception as e:
                logger.warning("entry-point skill %s failed: %s", ep.name, e)

    def _load_local_folder(self) -> None:
        path = get_settings().data_dir / "skills"
        if not path.exists():
            return
        for f in path.glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(f"openvox_local_{f.stem}", f)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr in dir(mod):
                    obj = getattr(mod, attr)
                    if isinstance(obj, type) and issubclass(obj, BaseSkill) and obj is not BaseSkill:
                        self.register(obj)
            except Exception as e:
                logger.warning("local skill %s failed to load: %s", f, e)


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _registry.discover()
    return _registry
