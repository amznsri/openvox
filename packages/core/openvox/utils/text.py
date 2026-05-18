"""Text helpers shared across the voice + telephony surface.

The big one is `clean_for_tts`: LLMs generate text optimised for a
reader, not a listener. Markdown emphasis, raw URLs, emoji, HTML
entities, and runs of punctuation all sound terrible when fed to a
TTS engine. Real symptoms we've hit on voice agents:

  - `**ListAssets**`           → "asterisk asterisk ListAssets ..."
  - `real-human guide`         → "real dash human guide"
  - `https://docs.example.com` → "h-t-t-p-s-colon-slash-slash docs..."
  - `✅ done`                  → "white-heavy-check-mark done" (or random)
  - `!!!`                      → splutters/repeats
  - `&amp;`                    → "ampersand a m p semicolon"

Two ways to fight all of this:
  1. Tell every agent's system_prompt "don't use markdown / urls /
     emoji". Works unevenly — the LLM still slips into formatting
     habits especially when summarising lists or APIs.
  2. Sanitise the text at the TTS boundary. Universal, no per-agent
     change, no LLM cooperation needed.

We do (2) here and call it from every place that emits TTS audio.
(1) is also a fine belt-and-braces addition in new system prompts —
but never rely on it alone.

Conservative scope by design — we only fix patterns where TTS reads
the literal characters in a way that destroys comprehension. We
deliberately *don't*:
  - Touch email addresses (TTS already says "user at example dot com").
  - Touch file extensions (".pdf" → "dot p d f" is acceptable and
    risk of clobbering sentence-ending periods is high).
  - Touch `&`, `@`, `#`, `~`, `|`, `^` (TTS reads each correctly in
    context — changing meaning would be worse than reading literally).
  - Touch slashes (URL vs path vs fraction needs context we don't
    have at this layer).
"""

from __future__ import annotations

import html
import re

# ── Markdown ────────────────────────────────────────────────────────
# Order matters: code blocks first (they may legitimately contain `*`
# chars we shouldn't touch), then bold (longest emphasis marker),
# then italic, then everything else.
_MD_CODE_BLOCK = re.compile(r"```([^`]*?)```", re.DOTALL)
_MD_CODE_INLINE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITALIC_STAR = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_MD_ITALIC_UNDER = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LIST_BULLET = re.compile(r"^[ \t]*[-*+]\s+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^[ \t]*>\s?", re.MULTILINE)
_MD_HR = re.compile(r"^[ \t]*([-*_])\1\1+[ \t]*$", re.MULTILINE)

# ── Hyphens ─────────────────────────────────────────────────────────
# Between alphabetic characters (compound words / product names like
# "real-human", "ModelArk-Byteplus"). The look-around requires alpha
# on BOTH sides, so dates "2026-05-18", ranges "10-20", negatives
# "-5", phone numbers "555-1234", and identifiers "T-1000" survive.
_HYPHEN_ALPHA = re.compile(r"(?<=[a-zA-Z])-(?=[a-zA-Z])")
# Em-dash-style separators → comma+space (TTS gives a natural pause).
_MULTI_DASH = re.compile(r"-{2,}")

# ── URLs ────────────────────────────────────────────────────────────
# Bare URLs in agent output. TTS spells them character-by-character
# ("h-t-t-p-s-colon-slash-slash..."). We strip them entirely — if
# the user needs the URL they can read the text version in their
# chat client (Telegram, etc.). Matches http://, https://, ftp://,
# and bare `www.` URLs. Trailing punctuation is preserved.
_URL = re.compile(
    r"\b(?:https?|ftp)://[^\s<>\"\)\]]+|\bwww\.[^\s<>\"\)\]]+",
    re.IGNORECASE,
)

# ── Emoji + other-symbol Unicode ────────────────────────────────────
# Most TTS engines either skip emoji entirely (silent gaps), spell
# the Unicode name ("white heavy check mark"), or trip on combining
# characters. Strip the common ranges. The set covers:
#   - Emoticons + faces (U+1F600–1F64F)
#   - Misc symbols + pictographs (U+1F300–1F5FF)
#   - Transport + map (U+1F680–1F6FF)
#   - Supplemental symbols/pictographs (U+1F900–1F9FF)
#   - Misc symbols (U+2600–26FF)
#   - Dingbats (U+2700–27BF)
#   - Variation selectors (U+FE00–FE0F) — these glue emoji together
#   - Zero-width joiner (U+200D)
_EMOJI = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "☀-⛿"
    "✀-➿"
    "︀-️"
    "‍"
    "]+",
    flags=re.UNICODE,
)

# ── Repeated punctuation ────────────────────────────────────────────
# `!!!` / `???` / `....` — TTS tends to spluttering repeats or extra
# silence. Collapse 3+ runs to 1.
_RUN_BANG = re.compile(r"!{3,}")
_RUN_QMARK = re.compile(r"\?{3,}")
_RUN_DOT = re.compile(r"\.{4,}")  # 4+ — ellipsis "..." is fine

# ── Whitespace normalisation ────────────────────────────────────────
_TAB = re.compile(r"\t+")
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ ]{2,}")


def clean_for_tts(text: str) -> str:
    """Sanitise LLM text for TTS synthesis.

    Removes / normalises voice-hostile patterns so the TTS engine reads
    natural prose, not the literal characters. See module docstring for
    the full list + rationale.

    Composable — safe to call on already-clean text. Idempotent in the
    sense that running it twice produces the same output as once.
    """
    if not text:
        return text
    t = text

    # 1. Decode HTML entities first — `&amp;` → `&`, `&lt;` → `<`, etc.
    # Without this, the literal "&amp;" reads as "ampersand a m p
    # semicolon". After decoding, the LLM-intended `&` reads naturally
    # as "and" (TTS handles standalone `&` correctly).
    t = html.unescape(t)

    # 2. Markdown — code blocks first (preserves API names inside),
    # then bold/italic, then structural elements.
    t = _MD_CODE_BLOCK.sub(lambda m: m.group(1).strip(), t)
    t = _MD_CODE_INLINE.sub(r"\1", t)
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_ITALIC_STAR.sub(r"\1", t)
    t = _MD_ITALIC_UNDER.sub(r"\1", t)
    t = _MD_HR.sub("", t)
    t = _MD_HEADER.sub("", t)
    t = _MD_IMAGE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_LIST_BULLET.sub("", t)
    t = _MD_BLOCKQUOTE.sub("", t)

    # Defensive sweep for orphan emphasis markers left over from
    # malformed LLM output (e.g. unmatched `**hello`). Only zap `**`
    # and `__` runs — keep solo `*` so arithmetic like "5 * 3" still
    # reads naturally.
    t = t.replace("**", "").replace("__", "")

    # 3. URLs — strip entirely (see module docstring).
    t = _URL.sub("", t)

    # 4. Emoji + variation selectors + zero-width joiners — strip.
    t = _EMOJI.sub("", t)

    # 5. Hyphens — order matters: collapse runs first (so the
    # alpha-alpha rule doesn't catch the inner `-` of `--`), then
    # single-hyphen compound words.
    t = _MULTI_DASH.sub(", ", t)
    t = _HYPHEN_ALPHA.sub(" ", t)

    # 6. Repeated punctuation — collapse to single instances.
    t = _RUN_BANG.sub("!", t)
    t = _RUN_QMARK.sub("?", t)
    t = _RUN_DOT.sub("...", t)  # cap at canonical ellipsis

    # 7. Whitespace normalisation.
    t = _TAB.sub(" ", t)
    t = _MULTI_BLANK.sub("\n\n", t)
    t = _MULTI_SPACE.sub(" ", t)

    return t.strip()


# Backwards-compat alias. Previously the helper was named after its
# original purpose (markdown stripping); it now does a lot more, but
# the old name still works so we don't break any in-tree callers.
strip_markdown_for_tts = clean_for_tts


# ── ASR helpers ─────────────────────────────────────────────────────


# STT engines sometimes emit very short tokens for background noise —
# a stray "uh", "mm", or single character. We treat these as
# "couldn't make out" rather than feeding them to the LLM (which
# wastes a turn). Threshold is character-count based since BytePlus
# returns variable-length tokens.
_MIN_USEFUL_TRANSCRIPT_CHARS = 2


def looks_like_real_speech(transcript: str) -> bool:
    """Cheap check: did the STT actually capture words, or is this noise?

    Used by telephony bridges before invoking the LLM — if the user
    coughed and got back "uh" we'd rather say "I couldn't catch that"
    than spend an LLM turn responding to noise.

    Conservative: returns True for anything 2+ chars containing at
    least one letter. False for pure punctuation, single chars, or
    empty.
    """
    if not transcript or not transcript.strip():
        return False
    cleaned = transcript.strip()
    if len(cleaned) < _MIN_USEFUL_TRANSCRIPT_CHARS:
        return False
    # Must contain at least one alphanumeric character — pure
    # punctuation like "..." or "!" is meaningless.
    if not any(c.isalnum() for c in cleaned):
        return False
    return True
