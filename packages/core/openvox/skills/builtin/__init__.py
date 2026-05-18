"""Built-in skills shipped with OpenVox.

Each module exports a `SKILLS = [...]` list of skill classes.
"""

__all__ = [
    "ecommerce",
    "education",
    "stock",
    "voice_analysis",
    "general",
    "documents",
    "reception",
    "sales",
    "language",
    # Session 10 — voice-driven agent creation. Used by the
    # built-in `setup-assistant` template; harmless to leave
    # registered on other agents (they just won't call these tools).
    "setup",
]
