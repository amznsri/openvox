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
        path = self.local_skills_dir()
        if path is None or not path.exists():
            return
        for f in path.glob("*.py"):
            try:
                # Use a stable module name keyed on the *resolved* path
                # so a re-load swaps the class table cleanly. Stem alone
                # collides if two files happen to share the same name
                # across different mounted dirs.
                mod_name = f"openvox_local_{f.stem}_{abs(hash(str(f.resolve()))) % 10_000_000}"
                spec = importlib.util.spec_from_file_location(mod_name, f)
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

    # ── Hot reload (Session 9) ───────────────────────────────────────
    def local_skills_dir(self) -> Path | None:
        """Return the directory we watch for hot-reload, or None if it
        hasn't been configured. Honours OPENVOX_SKILLS_DIR for users who
        want to point at their own folder (e.g. a shared volume in
        production) — falls back to data_dir/skills otherwise.
        """
        import os
        override = os.environ.get("OPENVOX_SKILLS_DIR", "").strip()
        if override:
            return Path(override).expanduser()
        s = get_settings()
        return Path(s.data_dir) / "skills"

    def reload_local(self) -> list[str]:
        """Re-scan the local folder and (re)register every skill found.

        Returns the list of skill ids that were re-loaded. We *don't*
        evict ids no longer in the folder — old VoiceSessions may still
        hold their classes via Python's GC. New sessions pick up the
        latest binding via `get(sid)` constructing a fresh instance.
        """
        before = set(self._classes.keys())
        # Drop cached *instances* — a hot-reload must give next callers
        # a freshly-constructed object so they pick up new `run()`
        # signatures, new `parameters` schemas, etc.
        with self._lock:
            self._instances.clear()
        self._load_local_folder()
        with self._lock:
            after = set(self._classes.keys())
        new = sorted(after - before)
        return new


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _registry.discover()
    return _registry
