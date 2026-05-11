"""Provider registry — discovers and caches singletons.

Built-in providers are registered at startup via `bootstrap.register_builtins()`.
Third-party packages can register via the `openvox.providers` setuptools
entry-point group (e.g. add to your pyproject.toml):

    [project.entry-points."openvox.providers"]
    my_stt = "my_pkg.stt:MyProvider"
"""

from __future__ import annotations

import importlib.metadata
import logging
from threading import RLock
from typing import TypeVar

from openvox.providers.base import Provider, ProviderType

logger = logging.getLogger(__name__)

P = TypeVar("P", bound=Provider)


class ProviderRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._instances: dict[tuple[ProviderType, str], Provider] = {}
        self._classes: dict[tuple[ProviderType, str], type[Provider]] = {}
        self._discovered_entrypoints = False

    # ── Registration ─────────────────────────────────────────────────
    def register(self, cls: type[Provider], *, instance: Provider | None = None) -> None:
        if not getattr(cls, "id", None) or not getattr(cls, "type", None):
            raise ValueError(f"{cls.__name__} must set `id` and `type` class attributes")
        key = (cls.type, cls.id)
        with self._lock:
            self._classes[key] = cls
            if instance is not None:
                self._instances[key] = instance

    # ── Discovery ────────────────────────────────────────────────────
    def discover_entrypoints(self) -> None:
        if self._discovered_entrypoints:
            return
        try:
            eps = importlib.metadata.entry_points(group="openvox.providers")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("entry_points lookup failed: %s", exc)
            return
        for ep in eps:
            try:
                cls = ep.load()
                if isinstance(cls, type) and issubclass(cls, Provider):
                    self.register(cls)
                    logger.info("registered third-party provider %s.%s", cls.type, cls.id)
            except Exception as exc:
                logger.warning("failed to load provider %s: %s", ep.name, exc)
        self._discovered_entrypoints = True

    # ── Lookup ───────────────────────────────────────────────────────
    def get(self, ptype: ProviderType | str, pid: str) -> Provider | None:
        if isinstance(ptype, str):
            ptype = ProviderType(ptype)
        key = (ptype, pid)
        with self._lock:
            inst = self._instances.get(key)
            if inst is not None:
                return inst
            cls = self._classes.get(key)
            if cls is None:
                return None
            inst = cls()  # type: ignore[call-arg]
            self._instances[key] = inst
            return inst

    def list(self, ptype: ProviderType | str | None = None) -> list[dict]:
        out: list[dict] = []
        with self._lock:
            for (t, pid), cls in self._classes.items():
                if ptype is not None and t != ProviderType(ptype):
                    continue
                # Try to construct so we can check availability without
                # raising on missing creds.
                try:
                    inst = self.get(t, pid)
                    available = inst.is_available() if inst else False
                except Exception:
                    available = False
                out.append(
                    {
                        "id": pid,
                        "type": t.value,
                        "display_name": getattr(cls, "display_name", pid),
                        "capabilities": [
                            c.value for c in getattr(cls, "capabilities", set()) or set()
                        ],
                        "available": available,
                    }
                )
        return sorted(out, key=lambda x: (x["type"], x["id"]))


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
