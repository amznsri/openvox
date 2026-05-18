"""Text helpers shared across the voice + telephony surface.

The big one is `strip_markdown_for_tts`: LLMs love wrapping emphasis
in Markdown (`**ListAssets**`, `_important_`, `` `query_documents` ``),
but TTS engines read those characters literally as "asterisk",
"underscore", "backtick". The result is the agent saying things like
"The list assets API is used astrix astrix ..." which is what surfaced
the first time we put a Doc Assistant agent on Telegram voice.

Two ways to fight this:
  1. Tell every agent's system_prompt "don't use markdown" — works
     unevenly because the LLM still slips into formatting habits,
     especially when summarising lists or code APIs.
  2. Strip markdown on the TTS side. Universal, no per-agent change,
     no LLM cooperation needed.

We do (2) here and call it from every place that calls
`tts.synthesize_*`. (1) is also a fine belt-and-braces addition in
new system prompts — but never rely on it alone.
"""

from __future__ import annotations

import re

# Order matters here — code blocks first (they may legitimately
# contain `*` chars we shouldn't touch), then bold (longest emphasis
# marker), then italic, then everything else.
_MD_CODE_BLOCK = re.compile(r"```([^`]*?)```", re.DOTALL)
_MD_CODE_INLINE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
# Italic: single `*` not preceded/followed by another `*`. Negative
# look-around stops us from butchering arithmetic like "5 * 3".
_MD_ITALIC_STAR = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_MD_ITALIC_UNDER = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LIST_BULLET = re.compile(r"^[ \t]*[-*+]\s+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^[ \t]*>\s?", re.MULTILINE)
_MD_HR = re.compile(r"^[ \t]*([-*_])\1\1+[ \t]*$", re.MULTILINE)

# Hyphens between alphabetic characters (compound words / product
# names like "real-human", "ModelArk-Byteplus"). TTS engines —
# BytePlus in particular — read these literally as "dash", so
# "real-human" becomes "real dash human". Bug surfaced via Doc
# Assistant reading back a filename like
# "Private real-human asset library guide--ModelArk-Byteplus.pdf".
#
# The look-around requires alpha on BOTH sides, so we *don't* mangle:
#   - dates: "2026-05-18"
#   - ranges: "10-20"
#   - negatives: "-5"
#   - phone numbers: "555-1234"
#   - mixed alphanumerics: "T-1000" (1 is digit, no match)
_HYPHEN_ALPHA = re.compile(r"(?<=[a-zA-Z])-(?=[a-zA-Z])")

# Runs of 2+ dashes (`--`, `---`, em-dash-ish) → comma+space so TTS
# gives it a natural pause rather than spelling out "dash dash".
_MULTI_DASH = re.compile(r"-{2,}")

# Collapse runs of whitespace introduced by stripping; preserve
# paragraph breaks (double newline) so sentence-flush TTS still has
# something to chunk on.
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def strip_markdown_for_tts(text: str) -> str:
    """Remove Markdown formatting characters that TTS would read literally.

    Specifically targets:
      - `**bold**` and `*italic*` / `_italic_`
      - `` `inline code` `` and ```` ```fenced``` ```` blocks
      - `# Headers` (any level)
      - `[link text](url)` → `link text` (url discarded — TTS would
        otherwise spell it out)
      - `- bullet` / `* bullet` / `+ bullet`
      - `> blockquote`
      - `---` / `***` horizontal rules

    Also handles voice-hostile punctuation that isn't strictly
    Markdown but trips every TTS engine the same way:
      - hyphens between letters ("real-human", "ModelArk-Byteplus") →
        space, so TTS doesn't read "real dash human".
      - runs of multiple dashes ("--", "---") → comma+space (natural
        pause).

    Deliberately *not* touching:
      - Standalone numbers like "5 * 3" — the negative-look-around in
        the italic regex skips arithmetic.
      - Numeric hyphens like dates ("2026-05-18"), ranges ("10-20"),
        negative numbers ("-5"), or phone numbers ("555-1234") — the
        hyphen-alpha regex requires alpha on BOTH sides.
      - URLs that appear bare (not in `[link](url)` form). They'd be
        spelled out, but that's the LLM's fault for emitting them in
        a voice reply.
      - Numbered lists like "1. First item" — the digit + dot reads
        naturally.
    """
    if not text:
        return text
    t = text
    # Code blocks: keep contents (LLMs often put API names in them).
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
    # Hyphen handling — order matters: collapse runs first (so the
    # alpha-alpha rule doesn't catch the inner `-` of `--`), then
    # single-hyphen compound words.
    t = _MULTI_DASH.sub(", ", t)
    t = _HYPHEN_ALPHA.sub(" ", t)
    t = _MULTI_BLANK.sub("\n\n", t)
    t = _MULTI_SPACE.sub(" ", t)
    return t.strip()
