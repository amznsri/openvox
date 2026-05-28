"""Truth-table tests for openvox.utils.text.

These four helpers are the load-bearing text-processing primitives
across the voice pipeline:

  * ``ReasoningStripper`` — streaming filter that removes
    ``<think>...</think>`` blocks across chunked LLM output
  * ``strip_reasoning_tags`` — non-streaming version of the same,
    used as a defensive final pass
  * ``clean_for_tts`` — sanitises LLM text before TTS synthesis
    (markdown, URLs, emoji, hyphens, repeated punctuation)
  * ``sanitize_user_final`` — strips STT-generated filler tokens
    from a user_final transcript without eating real speech

Bugs in any of these surface as audible regressions:

  - Leaked ``<think>`` tags in TTS audio → "less than think greater than..."
  - Unstripped markdown → "star star bold star star"
  - Aggressive STT filter → user says "Hi" and the agent ignores them
  - Lax STT filter → microphone breath becomes "嗯" and the LLM gets
    a no-op turn

So the test cases are derived from real production bugs we've seen
in Sessions 8-16, not from theoretical edge cases. Add a new case
here every time a text-processing bug ships in production.
"""
from __future__ import annotations

import pytest

from openvox.utils.text import (
    ReasoningStripper,
    clean_for_tts,
    looks_like_real_speech,
    sanitize_user_final,
    strip_reasoning_tags,
)


# ── strip_reasoning_tags (non-streaming) ────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # No tags → unchanged.
        ("Hello world", "Hello world"),
        ("", ""),
        # Complete <think>...</think> block — fully stripped.
        ("<think>plan: A then B</think>Answer is 42", "Answer is 42"),
        # Multiple blocks — all stripped.
        (
            "<think>step1</think>Yes<think>step2</think>",
            "Yes",
        ),
        # Orphan open tag at end (model cut off mid-think).
        ("Answer.<think>", "Answer."),
        # Orphan close — open got eaten upstream OR LLM only emitted close.
        ("Hello</think>", "Hello"),
        # Tag-like content that's NOT a reasoning tag (e.g. XML in answer).
        # Note: the regexes only match <think...> and <reasoning...>;
        # arbitrary tags like <foo> pass through unchanged.
        ("Use <foo> for X", "Use <foo> for X"),
        # Reasoning tag with hash suffix variant (observed on Seed-2-Pro).
        (
            "<think_neverhash>internal</think_neverhash>Visible",
            "Visible",
        ),
    ],
    ids=[
        "no-tags-passthrough",
        "empty-string",
        "complete-block-stripped",
        "multiple-blocks-stripped",
        "orphan-open-at-end",
        "orphan-close-at-start",
        "non-reasoning-tag-preserved",
        "hash-suffix-variant",
    ],
)
def test_strip_reasoning_tags(raw: str, expected: str) -> None:
    assert strip_reasoning_tags(raw) == expected


def test_strip_reasoning_tags_idempotent() -> None:
    """Running twice produces same as running once — safety net for
    multi-pass text pipelines."""
    raw = "<think>x</think>Final answer"
    once = strip_reasoning_tags(raw)
    twice = strip_reasoning_tags(once)
    assert once == twice == "Final answer"


# ── ReasoningStripper (streaming) ──────────────────────────────────
#
# The streaming case is where most real bugs live. LLM tokens arrive
# in awkward boundaries: `<th` + `ink>` + `internal</thi` + `nk>`.
# A naïve per-chunk regex leaks pieces. These tests intentionally
# fragment input across tag boundaries.


def test_reasoning_stripper_passthrough() -> None:
    s = ReasoningStripper()
    assert s.feed("Hello ") == "Hello "
    assert s.feed("world") == "world"
    assert s.flush() == ""


def test_reasoning_stripper_full_block_one_chunk() -> None:
    s = ReasoningStripper()
    assert s.feed("<think>plan</think>Answer") == "Answer"
    assert s.flush() == ""


def test_reasoning_stripper_split_across_open_tag() -> None:
    """Open tag arrives in two pieces — `<th` then `ink>plan</think>X`."""
    s = ReasoningStripper()
    # First chunk has a partial-looking-like-open; must hold back.
    assert s.feed("<th") == ""
    assert s.feed("ink>plan</think>") == ""
    assert s.feed("X") == "X"
    assert s.flush() == ""


def test_reasoning_stripper_split_across_close_tag() -> None:
    """Close tag fragmented across multiple chunks."""
    s = ReasoningStripper()
    assert s.feed("<think>plan</thi") == ""
    assert s.feed("nk>Answer") == "Answer"
    assert s.flush() == ""


def test_reasoning_stripper_unclosed_at_eof_drops_content() -> None:
    """LLM was truncated mid-think — content inside is unsafe to surface."""
    s = ReasoningStripper()
    assert s.feed("<think>partial reason") == ""
    # flush drops the un-closed content rather than leaking it
    assert s.flush() == ""


def test_reasoning_stripper_text_then_tag_then_text() -> None:
    s = ReasoningStripper()
    assert s.feed("Before ") == "Before "
    assert s.feed("<think>x</think>") == ""
    assert s.feed("After") == "After"
    assert s.flush() == ""


def test_reasoning_stripper_orphan_close_dropped() -> None:
    """Stray </think> with no matching open — must be silently dropped."""
    s = ReasoningStripper()
    assert s.feed("Visible </think>more") == "Visible more"
    assert s.flush() == ""


# ── clean_for_tts ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Markdown bold / italic — markers stripped, content preserved.
        ("**bold** and *italic*", "bold and italic"),
        ("Use __strong__ here", "Use strong here"),
        # Inline code — backticks stripped, content preserved.
        ("Call `func()` then", "Call func() then"),
        # Headers + lists — leading markup stripped.
        ("# Title", "Title"),
        ("- item one", "item one"),
        # URLs — stripped entirely (TTS reading them is unintelligible).
        ("See https://example.com for details", "See for details"),
        # Emoji — stripped.
        ("Done 🎉 ready 👍", "Done ready"),
        # HTML entities — decoded BEFORE markdown removal.
        ("AT&amp;T is &lt;cool&gt;", "AT&T is <cool>"),
        # Repeated punctuation — collapsed.
        ("Really???", "Really?"),
        ("Wait....", "Wait..."),
        ("Yes!!!!", "Yes!"),
        # Reasoning tags — stripped (defense-in-depth; the streaming
        # stripper is the primary, this is the safety net).
        ("<think>x</think>Real answer", "Real answer"),
        # Whitespace normalisation.
        ("Hello\t\tworld", "Hello world"),
        # Empty / None-like cases.
        ("", ""),
    ],
    ids=[
        "markdown-bold-italic",
        "markdown-strong-underscore",
        "inline-code",
        "header",
        "bullet-list",
        "url-stripped",
        "emoji-stripped",
        "html-entities-decoded",
        "triple-question",
        "quadruple-dot",
        "quadruple-bang",
        "reasoning-tag-defense",
        "tab-normalised",
        "empty-string",
    ],
)
def test_clean_for_tts(raw: str, expected: str) -> None:
    assert clean_for_tts(raw) == expected


def test_clean_for_tts_idempotent() -> None:
    """Running twice produces same as once — composes safely with
    other pipeline stages."""
    raw = "**bold** with [link](https://x.com) 🎉"
    once = clean_for_tts(raw)
    twice = clean_for_tts(once)
    assert once == twice


# ── sanitize_user_final ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, lang, expected_text, expected_dropped_reason_substr",
    [
        # Real English speech on English agent — passes through unchanged.
        ("Hello there", "en-US", "Hello there", None),
        # Two-char "Hi" — must pass (real greeting).
        ("Hi", "en-US", "Hi", None),
        # Leading Chinese filler on English agent — trimmed.
        ("嗯。create a new agent", "en-US", "create a new agent", None),
        # Trailing filler — trimmed.
        ("Search the web 啊", "en-US", "Search the web", None),
        # Pure filler → dropped with reason.
        ("嗯。", "en-US", None, "pure filler"),
        # Single-char CJK on English agent → dropped (too short).
        ("嗯", "en-US", None, None),  # multiple possible reasons
        # Empty / whitespace-only → dropped.
        ("", "en-US", None, "empty"),
        ("   ", "en-US", None, "empty"),
        # Chinese agent — 嗯 is legitimate; passes through unchanged.
        ("嗯", "zh-CN", "嗯", None),
        ("嗯，你好", "zh-CN", "嗯，你好", None),
        # Mid-utterance filler — NOT trimmed (only affixes).
        (
            "Search web 嗯 for news",
            "en-US",
            "Search web 嗯 for news",
            None,
        ),
    ],
    ids=[
        "english-real-speech",
        "english-two-char-Hi",
        "leading-cjk-filler-trimmed",
        "trailing-cjk-filler-trimmed",
        "pure-filler-dropped",
        "single-cjk-char-dropped",
        "empty-string-dropped",
        "whitespace-only-dropped",
        "chinese-agent-filler-preserved",
        "chinese-agent-utterance-preserved",
        "mid-utterance-filler-preserved",
    ],
)
def test_sanitize_user_final(
    raw: str,
    lang: str,
    expected_text: str | None,
    expected_dropped_reason_substr: str | None,
) -> None:
    text, reason = sanitize_user_final(raw, lang)
    assert text == expected_text, f"text mismatch for {raw!r}: got {text!r}"
    if expected_text is None and expected_dropped_reason_substr:
        assert expected_dropped_reason_substr in reason


def test_sanitize_user_final_none_input() -> None:
    """None or falsy text → drop with 'empty' reason."""
    text, reason = sanitize_user_final("", "en-US")
    assert text is None
    assert reason == "empty"


# ── looks_like_real_speech ─────────────────────────────────────────


@pytest.mark.parametrize(
    "transcript, expected",
    [
        ("Hello", True),
        ("Hi", True),  # 2-char threshold is exactly the boundary
        ("a", False),  # 1-char fails the threshold
        ("", False),
        ("   ", False),
        # Long noise transcript with real content.
        ("Where is my order", True),
    ],
    ids=[
        "five-char-word",
        "two-char-greeting-boundary",
        "one-char-fails",
        "empty-fails",
        "whitespace-only-fails",
        "long-real-question",
    ],
)
def test_looks_like_real_speech(transcript: str, expected: bool) -> None:
    assert looks_like_real_speech(transcript) is expected
