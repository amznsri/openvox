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
    # Session 18 Phase 1.4 — native Gmail + Calendar skills backed
    # by the OAuth token store. Replaces the MCP-based Gmail/Calendar
    # path that templates used in Sessions 16-17.
    "google_workspace",
]
