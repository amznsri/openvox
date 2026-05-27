"""Regression guard: every shipped template must carry the shared
voice-style header (v0.2.29 voice-prompting hardening pass).

What this guards against:
  - A new template added to TEMPLATES forgetting to wrap its
    `system_prompt` with `_with_voice_style(...)`. That template
    would silently violate the brevity / no-formatting /
    pronunciation / safety rules the rest of the catalogue
    enforces — and the operator who instantiates it would get
    a markdown-spewing verbose agent that doesn't match the
    others.
  - A maintainer removing the header from an existing template
    (e.g. as part of a "trim the system prompt" pass) — the
    header is short on purpose; trimming should be elsewhere.
  - Drift between `_VOICE_STYLE_HEADER` text and what's actually
    embedded in template prompts (the test pins the FIRST LINE
    of the header as a sentinel; anything that re-renders the
    header still satisfies the check).

What this doesn't guard against:
  - Quality of the per-template content. That's a human-review
    concern.
  - The header's content itself (the rules can change; this test
    only enforces that whatever shape the header has, every
    template carries it).
"""

from __future__ import annotations

import pytest


def _header_marker() -> str:
    """First non-blank line of the shared header — used as a
    sentinel substring. Splitting on lines is more robust than
    a fixed-string match because the header may grow rules
    without breaking the test."""
    from openvox.api.routes.templates import _VOICE_STYLE_HEADER

    for line in _VOICE_STYLE_HEADER.splitlines():
        s = line.strip()
        if s:
            return s
    raise RuntimeError("_VOICE_STYLE_HEADER is empty — that's a bug")


def test_every_template_has_voice_style_header():
    """Every dict in TEMPLATES has a system_prompt that contains
    the shared voice-style header marker."""
    from openvox.api.routes.templates import TEMPLATES

    marker = _header_marker()
    missing = []
    for t in TEMPLATES:
        prompt = (t.get("default") or {}).get("system_prompt", "") or ""
        if marker not in prompt:
            missing.append(t.get("name") or t.get("id"))
    assert not missing, (
        f"templates missing the shared voice-style header: {missing!r}. "
        f"Wrap each system_prompt with `_with_voice_style(...)` so the "
        f"brevity / no-formatting / safety rules apply consistently."
    )


def test_setup_assistant_prompt_has_voice_style_header():
    """The Setup Assistant is the agent that builds OTHER agents —
    it has to model the voice-style behaviour itself. Its prompt
    is composed slightly differently (text.format on a template),
    so check it independently."""
    from openvox.api.routes.templates import _SETUP_ASSISTANT_PROMPT

    marker = _header_marker()
    assert marker in _SETUP_ASSISTANT_PROMPT, (
        "Setup Assistant prompt lost its voice-style header. "
        "The header should be prepended to _SETUP_ASSISTANT_PROMPT_TEMPLATE."
    )


def test_voice_style_header_token_budget_under_300():
    """Soft cap on the header size — every voice turn pays this
    in first-token latency. If the header grows past ~300 tokens
    (~1200 chars) someone is over-engineering it.

    Using a 4-chars-per-token heuristic that overestimates short
    English. The real token count will be lower; this is a
    sanity bound, not an exact tokeniser run.
    """
    from openvox.api.routes.templates import _VOICE_STYLE_HEADER

    chars = len(_VOICE_STYLE_HEADER)
    rough_tokens = chars // 4
    assert rough_tokens < 300, (
        f"voice-style header is ~{rough_tokens} tokens "
        f"({chars} chars) — over the 300-token soft cap. Trim "
        f"non-essential bullets before merging."
    )


def test_email_assistant_does_not_request_numbered_lists():
    """The pre-v0.2.29 Email Assistant prompt explicitly instructed
    the LLM to 'Use a numbered list format for multi-thread
    summaries' — that's the exact anti-pattern the no-formatting
    rule guards against (lists get read aloud or break TTS pacing).
    The hardening pass removed it. This test pins the removal
    so a future revert is caught.
    """
    from openvox.api.routes.templates import _EMAIL_ASSISTANT_PROMPT

    assert "numbered list format" not in _EMAIL_ASSISTANT_PROMPT.lower(), (
        "Email Assistant prompt has re-introduced the 'numbered list "
        "format' instruction. That's a TTS anti-pattern — lists get "
        "read aloud with awkward pauses. Use spoken connectors "
        "('First… then… also…') instead."
    )
