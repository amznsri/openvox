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

# ── Reasoning / chain-of-thought tags ───────────────────────────────
# Modern reasoning models (Seed-2-Pro, DeepSeek-R1, Claude-with-extended-
# thinking, ...) emit their chain-of-thought inside XML-ish tags. Each
# vendor decorates the close differently to make prompt injection
# harder — Seed-2 in particular appends a per-session random hash
# (`</think_never_used_51bce0c785ca2f68081bfa7d91973934>`). The tags
# must NEVER reach:
#   - the chat UI (renders raw, looks like a security leak)
#   - the TTS engine (would literally read "less than slash think
#     underscore never underscore used underscore five one b c …")
#   - the LLM history (model sees its own reasoning, gets confused,
#     and the input cost explodes)
#
# Regex matches `<think>...</think_HASH>` AND `<reasoning>...</...>`
# in both nested and orphan forms. DOTALL so multi-line reasoning is
# captured.
_REASONING_BLOCK = re.compile(
    r"<(think|reasoning)(?:[^>]*)>.*?</(?:think|reasoning)(?:[^>]*)>",
    re.DOTALL | re.IGNORECASE,
)
# Orphan tags — happen when the LLM stream is truncated mid-block, or
# when only the close arrives (the open got eaten by an earlier filter).
# We've actually observed both forms in production: standalone
# `</think_never_used_HASH>` AND naked `<think>` at end-of-message.
_REASONING_ORPHAN_OPEN = re.compile(r"<(?:think|reasoning)(?:[^>]*)>", re.IGNORECASE)
_REASONING_ORPHAN_CLOSE = re.compile(r"</(?:think|reasoning)(?:[^>]*)>", re.IGNORECASE)


def strip_reasoning_tags(text: str) -> str:
    """Remove reasoning blocks and any orphan reasoning tags.

    Use this as a DEFENSIVE final pass — the primary defense is the
    streaming `ReasoningStripper` in the orchestrator's LLM loop, which
    catches tags as they arrive token-by-token. This helper exists for
    callers that already have the FULL response in hand (telephony,
    non-streaming /turn endpoint, eval harness, etc.).

    Idempotent. Safe to run on text that has no reasoning tags at all.
    """
    if not text:
        return text
    t = _REASONING_BLOCK.sub("", text)
    t = _REASONING_ORPHAN_OPEN.sub("", t)
    t = _REASONING_ORPHAN_CLOSE.sub("", t)
    return t


class ReasoningStripper:
    """Streaming filter that removes <think>…</think_HASH> blocks across
    chunked LLM output.

    Token boundaries land mid-tag almost every turn — sometimes the
    open tag arrives in three pieces like `<th` + `ink_neve` + `r…>`
    — so a naïve `re.sub(...)` on each chunk leaks pieces. This class
    holds a small internal buffer until tag boundaries are resolved.

    Usage:

        s = ReasoningStripper()
        for chunk in stream:
            clean_chunk = s.feed(chunk)
            if clean_chunk:
                yield clean_chunk
        tail = s.flush()
        if tail:
            yield tail

    State machine:
      - outside: pass text through, but hold back any trailing `<…`
        that could become an open tag once more text arrives.
      - inside:  buffer everything, looking for the matching close;
        emit nothing until the close is found, then resume outside.

    Unclosed reasoning at end-of-stream is dropped on `flush()` — we
    assume the LLM was cut off mid-think and the content is unsafe
    to surface.
    """

    _MAYBE_OPEN_PREFIXES = ("<think", "<reasoning")

    def __init__(self) -> None:
        self._held: str = ""
        self._inside: bool = False

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        self._held += delta
        out: list[str] = []
        while self._held:
            if self._inside:
                m = _REASONING_ORPHAN_CLOSE.search(self._held)
                if not m:
                    return "".join(out)
                self._held = self._held[m.end():]
                self._inside = False
                continue
            lt = self._held.find("<")
            if lt < 0:
                out.append(self._held)
                self._held = ""
                return "".join(out)
            if lt:
                out.append(self._held[:lt])
                self._held = self._held[lt:]
            gt = self._held.find(">")
            if gt < 0:
                if self._looks_like_partial_open(self._held):
                    return "".join(out)
                out.append("<")
                self._held = self._held[1:]
                continue
            tag = self._held[: gt + 1]
            if _REASONING_ORPHAN_OPEN.match(tag):
                self._held = self._held[gt + 1:]
                self._inside = True
                continue
            if _REASONING_ORPHAN_CLOSE.match(tag):
                # Orphan close — open got eaten upstream OR LLM only
                # emitted the close (we observed this on Seed-2-Pro
                # when reasoning was truncated). Drop the close, stay
                # in outside-mode.
                self._held = self._held[gt + 1:]
                continue
            out.append(tag)
            self._held = self._held[gt + 1:]
        return "".join(out)

    def flush(self) -> str:
        if self._inside:
            self._held = ""
            self._inside = False
            return ""
        out = self._held
        self._held = ""
        return out

    @classmethod
    def _looks_like_partial_open(cls, s: str) -> bool:
        low = s.lower()
        for prefix in cls._MAYBE_OPEN_PREFIXES:
            if prefix.startswith(low) or low.startswith(prefix):
                return True
        return False


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

    # 0. Strip any reasoning tags that escaped the streaming filter.
    # The primary defense is `ReasoningStripper` in the LLM stream
    # loop; this is the last-line safety net so a leaked tag never
    # becomes audible. Safe + idempotent on text that has no tags.
    t = strip_reasoning_tags(t)

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

# Mandarin filler tokens BytePlus Seed-ASR and most multilingual ASRs
# emit on silence / breath / lip smacks. Used as a character class for
# leading/trailing trim so "嗯。create" → "create" and "啊 search web"
# → "search web". Single chars + punctuation only — multi-char fillers
# like "嗯嗯嗯" are caught by the CJK-floor check in sanitize_user_final.
_ASR_FILLER_CHARS = "嗯啊哦哎唉呃呀诶嗨哼唔"
# CJK punctuation — never appears in legitimate English, so safe to
# strip aggressively. ASCII punctuation (.,!?;:) is DELIBERATELY
# excluded here — "Search web." legitimately ends with a period and
# we must not eat it. Residual non-alnum-only output after this trim
# falls through to the post-trim check.
_CJK_PUNCT = "。、，！？；：「」『』（）【】〔〕…—–·"
_WS = " \t\n\r　"
_ASR_FILLER_STRIP_CHARS = _ASR_FILLER_CHARS + _CJK_PUNCT + _WS

# Explicit blocklist of complete filler tokens (with all punctuation
# variants we've observed). Kept separate so the truth-table tests can
# assert exact-match behaviour.
_ASR_FULL_FILLER_TOKENS: frozenset[str] = frozenset({
    "嗯", "啊", "哦", "哎", "唉", "呃", "呀", "诶", "嗨", "哼", "唔",
    "嗯嗯", "啊啊", "哦哦", "嗯嗯嗯",
    "嗯。", "啊。", "哦。", "哎。", "唉。", "呃。", "呀。", "诶。", "嗨。", "哼。", "唔。",
    "嗯.", "啊.", "哦.",
    "嗯，", "啊，", "哦，",
})


_ALL_PUNCT_AND_WS = _CJK_PUNCT + _WS + ".,!?;:\"'()[]{}<>-_/\\|@#$%^&*+="


def _is_pure_cjk(s: str) -> bool:
    """True iff every non-whitespace, non-punct char is CJK."""
    stripped = "".join(c for c in s if c not in _ALL_PUNCT_AND_WS)
    if not stripped:
        return False
    return all(
        0x3000 <= ord(c) <= 0x9FFF
        or 0x3400 <= ord(c) <= 0x4DBF
        or 0xF900 <= ord(c) <= 0xFAFF
        for c in stripped
    )


def sanitize_user_final(text: str, agent_language: str | None) -> tuple[str | None, str]:
    """Clean up an STT user_final transcript.

    Returns (cleaned_text_or_None, reason).
      - cleaned_text == None → drop this final entirely (was pure
        hallucination, log it for debugging).
      - cleaned_text == text → no change needed.
      - cleaned_text != text → trimmed (leading/trailing filler
        characters were removed but real content remains).

    Defence layers, applied in order:

      1. Empty after whitespace strip → drop (the caller should
         already short-circuit on empty, but harmless to re-check).
      2. agent_language starts with "zh-" → the agent IS Chinese.
         嗯/啊 are legitimate. Return unchanged.
      3. Strip leading + trailing filler characters + their attendant
         punctuation. After this:
            "嗯。create"        → "create"
            "啊 search web"     → "search web"
            "嗯。"              → ""  (full filler, falls to step 4)
            "Where is order?"  → "Where is order?" (unchanged)
      4. After trimming, if the result is empty OR is itself a known
         filler OR is ≤3-char pure CJK on a non-Chinese agent → drop.
      5. Otherwise return the trimmed text.

    Real user-speech invariants this preserves:
      - English ≥2-char utterances ("Hi", "OK", "Yes") pass through
        unchanged (no CJK chars, no leading filler).
      - Long Chinese utterances on zh agents pass through unchanged.
      - Mid-utterance filler stays: "Search web... 嗯 the news" is
        passed through; we only trim AFFIXES, never the middle, to
        avoid mangling real speech that happens to contain a 嗯
        the user actually said.
    """
    if not text or not text.strip():
        return None, "empty"
    lang = (agent_language or "").lower()
    if lang.startswith("zh"):
        # Chinese-first agent — trust the ASR. 嗯/啊 are real turns.
        return text, ""

    # Trim filler + CJK punctuation + whitespace from both ends.
    # ASCII punctuation is deliberately excluded — "Search web."
    # legitimately ends with a period and we must not eat it.
    trimmed = text.strip(_ASR_FILLER_STRIP_CHARS)

    if not trimmed:
        return None, f"pure filler after trim: {text!r}"
    # Common ASR mashup: "嗯," → after trimming "嗯" → "," — the
    # comma is meaningless on its own. Drop anything that has no
    # alphanumeric character (Latin or Han) at all.
    if not any(c.isalnum() for c in trimmed):
        return None, f"non-alnum residue after trim: {trimmed!r}"
    if trimmed in _ASR_FULL_FILLER_TOKENS:
        return None, f"blocklisted filler: {trimmed!r}"
    if len(trimmed) <= 3 and _is_pure_cjk(trimmed):
        return None, f"≤3-char pure-CJK on {lang or 'non-zh'} agent: {trimmed!r}"
    return trimmed, ""


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
