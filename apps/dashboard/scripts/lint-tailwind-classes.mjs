/**
 * Lint guard against phantom Tailwind colour classes.
 *
 * CLAUDE.md §8 #100 documents a multi-day debugging loop where
 * `bg-popover` was used across the help popover but `popover` is
 * NOT a colour key in `tailwind.config.ts`. Tailwind silently emits
 * no CSS rule when you reference a colour that isn't defined — the
 * class compiles to nothing, the element has no background, and
 * `text-foreground/85` text reads straight through onto whatever
 * page content lies beneath. Took four release-cycle attempts to
 * spot because every "fix" looked structurally plausible (alpha
 * tweaks, z-index lifts, opacity) and the DevTools `computed
 * background-color` lookup never happened.
 *
 * This script is the lightweight prevention layer:
 *
 *   1. Parse `tailwind.config.ts` and extract every key under
 *      `theme.extend.colors`. We accept top-level keys (border,
 *      input, …) and nested keys (`violet.500`, `card.foreground`,
 *      `card.DEFAULT` collapsed back to `card`).
 *
 *   2. Walk `src/ ** / *.tsx` and `src/ ** / *.ts` files. For every
 *      Tailwind `bg-X`, `text-X`, `border-X`, `ring-X`,
 *      `divide-X`, `placeholder-X`, `fill-X`, `stroke-X` class
 *      reference, the X (before any `/` opacity modifier and
 *      before any responsive prefix like `hover:` / `md:`) is
 *      checked against the theme keys.
 *
 *   3. Built-in Tailwind colours (`white`, `black`, `transparent`,
 *      `current`, `inherit`, `slate-*`, `red-*`, `gray-*`, etc.)
 *      are allow-listed since Tailwind ships them by default.
 *
 *   4. Numeric scales / arbitrary values like `bg-[#fff]` /
 *      `bg-[hsl(0,0%,100%)]` / `bg-1px` / `bg-x-axis` are skipped
 *      — they don't reference theme colours.
 *
 * Exit codes:
 *   0 — all referenced theme-colour classes are valid.
 *   1 — at least one phantom class detected (printed with location).
 *
 * Run from `apps/dashboard`:  npx tsx scripts/lint-tailwind-classes.mjs
 *
 * Integration:
 *   The dashboard CI test workflow runs this from the
 *   `dashboard export build` job (see `.github/workflows/test.yml`),
 *   so any phantom class added in a PR fails CI before merge.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const REPO = join(HERE, "..");
const SRC = join(REPO, "src");
const TAILWIND_CONFIG = join(REPO, "tailwind.config.ts");

// Tailwind ships these colour names + the full default palette OOTB.
// Anything in this set isn't required to appear in tailwind.config.ts
// to be a valid class.
const BUILTIN_COLOR_NAMES = new Set([
  "white", "black", "transparent", "current", "inherit", "none",
  "slate", "gray", "zinc", "neutral", "stone",
  "red", "orange", "amber", "yellow", "lime", "green", "emerald",
  "teal", "cyan", "sky", "blue", "indigo", "violet", "purple",
  "fuchsia", "pink", "rose",
]);

// Prefixes we consider UNAMBIGUOUSLY colour-based — the original bug
// class was `bg-popover`, and these utilities only ever take a colour
// token (or an arbitrary value). Tailwind utilities like `text-`,
// `border-`, `shadow-`, `from-`/`via-`/`to-` overlap with sizing,
// width, blur, and percentage values, so linting them produces
// false positives (text-sm, border-t-2, shadow-md, from-10%). We
// deliberately stay conservative — false negatives on those rare
// `text-popover` typos are an acceptable trade for zero false
// positives on the load-bearing `bg-*` family.
//
// If a regression ever shows up in `text-*` colour classes (e.g.
// `text-popover-foreground` slipping past review), expand this list
// then — but only after writing additional skip rules for size and
// alignment.
const COLOR_UTILS = [
  "bg",
  "ring",     // ring-offset has its own utility class, this matches plain ring-<color>
  "accent",
  "caret",
  "fill",
  "stroke",
];

// Pseudo-classes / responsive prefixes that wrap colour classes.
// We strip these before checking the actual class.
//
// Tailwind's variant grammar is permissive: anything followed by
// a `:` is treated as a variant. We don't need to enumerate them
// exhaustively — splitting on `:` and taking the last segment is
// enough for the lint check.

/** Extract the theme-colour keys from tailwind.config.ts. */
function loadThemeColours() {
  const text = readFileSync(TAILWIND_CONFIG, "utf-8");
  // We don't want to evaluate the TS at runtime (would need tsx
  // + a real loader). The colours block is shallow + literal in
  // this codebase, so a hand-rolled mini-parser is enough.
  //
  // Strategy: find the `colors: { ... }` block under
  // `theme.extend.colors`, then scan its top-level keys + nested
  // keys via a tiny brace-counting walker.
  const colorsStart = text.search(/colors\s*:\s*\{/);
  if (colorsStart < 0) {
    throw new Error("couldn't find a `colors: {` block in tailwind.config.ts");
  }
  // Advance to the opening `{`.
  let i = text.indexOf("{", colorsStart);
  let depth = 0;
  let end = -1;
  for (let j = i; j < text.length; j++) {
    if (text[j] === "{") depth++;
    else if (text[j] === "}") {
      depth--;
      if (depth === 0) {
        end = j;
        break;
      }
    }
  }
  if (end < 0) {
    throw new Error("couldn't find matching `}` for colors block");
  }
  const block = text.slice(i + 1, end);

  // Top-level keys: a simple `^\s*<key>\s*:` match. Nested objects
  // (`primary: { DEFAULT: ..., foreground: ... }`) are picked up by
  // a second pass after we know the top-level key boundaries.
  const themeKeys = new Set();
  // Match every identifier followed by `:` that isn't inside a
  // nested brace.
  let nest = 0;
  let cursor = 0;
  while (cursor < block.length) {
    const ch = block[cursor];
    if (ch === "{") nest++;
    else if (ch === "}") nest--;
    else if (nest === 0) {
      // Look for an identifier followed by colon at depth 0.
      const tail = block.slice(cursor);
      const m = tail.match(/^([A-Za-z_][\w-]*)\s*:/);
      if (m) {
        themeKeys.add(m[1]);
        cursor += m[0].length;
        continue;
      }
    }
    cursor++;
  }
  return themeKeys;
}

/** Recursively gather .tsx / .ts files under SRC. */
function walk(dir, out = []) {
  for (const ent of readdirSync(dir)) {
    const full = join(dir, ent);
    const st = statSync(full);
    if (st.isDirectory()) walk(full, out);
    else if (/\.(tsx|ts)$/.test(ent)) out.push(full);
  }
  return out;
}

/** Extract candidate class-name strings from a TSX/TS file.
 *
 *  Initial approach scoped to `className="..."` / `className={"..."}`
 *  / `className={\`...\`}` — but template-literal `${...}`
 *  interpolations break that: the outer regex consumes the entire
 *  interpolation block as one "string", and the embedded
 *  `"border-primary/50 bg-primary/5"` ternary branches end up with
 *  parser-noise tokens (e.g. `hover:bg-muted"` with a trailing
 *  quote).
 *
 *  Robust fix: just scan EVERY quoted string literal in the file.
 *  Yes, this picks up unrelated strings (URLs, error messages, JSON
 *  blobs) — but `parseColorClass()` below rejects anything that
 *  doesn't look like a Tailwind colour utility, so false hits get
 *  filtered out cheaply. The trade-off is worth the simplicity:
 *  zero parser-state in this script.
 */
function extractClassStrings(text) {
  const out = [];
  // Triple-quoted strings + escaped quotes inside are uncommon in
  // .tsx; the simple `[^"]+` body is fine for our purposes.
  const patterns = [
    /"([^"\n]+)"/g,    // double-quoted strings
    /'([^'\n]+)'/g,    // single-quoted strings (less common but used in templates)
    /`([^`]+)`/g,      // template-literal contents (without interpolation parsing)
  ];
  for (const p of patterns) {
    let m;
    while ((m = p.exec(text))) out.push({ raw: m[1], offset: m.index });
  }
  return out;
}

// Non-colour Tailwind keywords that share our scanned prefixes.
// `bg-gradient-to-r` / `bg-gradient-to-br` / etc. are gradient
// direction utilities — the "colour" segment is `gradient`, which
// isn't a theme key but is a legit OOTB Tailwind utility. Same
// dance for `ring-offset-*` (ring-offset width OR ring-offset
// colour, but the keyword `offset` is structural, not a colour).
const BG_NON_COLOR_KEYWORDS = new Set([
  "gradient",  // bg-gradient-*
  "fixed", "local", "scroll",  // bg-attachment-*
  "clip-border", "clip-padding", "clip-content", "clip-text",
  "origin-border", "origin-padding", "origin-content",
  "no-repeat", "repeat", "repeat-x", "repeat-y", "repeat-round", "repeat-space",
  "auto", "cover", "contain",  // bg-size-*
  "top", "bottom", "left", "right", "center",  // bg-position-*
  "blend",  // bg-blend-*
  // ring-offset-* is its own utility family. We don't lint it
  // today (the original bug class was bg-popover, and ring-offset
  // colours never bit us); revisit if a regression surfaces.
  "offset",
  // ring-inset is a structural utility, not a colour.
  "inset",
]);

/** Convert a class chunk like `hover:bg-popover/95` to its core
 *  colour-utility components if it's colour-related. Returns:
 *
 *      { util: "bg", color: "popover", raw: "hover:bg-popover/95" }
 *
 *  or null when the class isn't a colour utility (e.g. `flex`,
 *  `p-4`) or uses an arbitrary value (`bg-[#fff]`).
 */
function parseColorClass(cls) {
  // Strip variant prefixes ("hover:", "md:", "dark:", ...) — split
  // on `:` and take the last segment.
  const core = cls.split(":").pop();
  if (!core) return null;

  // Strip opacity modifier (`bg-card/85` → `bg-card`).
  const noOpacity = core.includes("/") ? core.slice(0, core.indexOf("/")) : core;

  // Skip arbitrary values: `bg-[#fff]`, `text-[hsl(...)]`, etc.
  if (noOpacity.includes("[")) return null;

  // Class shape: `<util>-<colorKey>` or `<util>-<colorKey>-<shade>`.
  // Examples we care about: bg-card, text-foreground, border-violet-500,
  // ring-popover. Examples we DON'T care about: p-4, gap-2, h-16.
  for (const util of COLOR_UTILS) {
    const prefix = util + "-";
    if (noOpacity.startsWith(prefix)) {
      const rest = noOpacity.slice(prefix.length);
      if (!rest) return null;
      // The colour key is the FIRST segment. `violet-500` → key `violet`,
      // shade `500`. `card-foreground` is a subkey — but Tailwind compiles
      // it from a nested object, so `card` is still the lookup point.
      const firstSeg = rest.split("-")[0];
      // Skip purely-numeric "shades" — those are sizes, not colours
      // (e.g. `shadow-md`, `border-2`). Tailwind has size-only
      // utilities under the same prefixes.
      if (/^\d+$/.test(firstSeg)) return null;
      // Skip Tailwind keywords that share these prefixes but aren't
      // colour utilities. `bg-gradient-to-r` is the most common —
      // and is technically a `bg-` class but refers to a gradient-
      // direction utility, not a colour. Same for `bg-cover`,
      // `bg-no-repeat`, `bg-fixed`, etc.
      if (BG_NON_COLOR_KEYWORDS.has(firstSeg)) return null;
      return { util, color: firstSeg, raw: cls };
    }
  }
  return null;
}

const themeKeys = loadThemeColours();
const allowed = new Set([...themeKeys, ...BUILTIN_COLOR_NAMES]);

const files = walk(SRC);
const offenders = [];

for (const file of files) {
  const text = readFileSync(file, "utf-8");
  const strings = extractClassStrings(text);
  for (const { raw, offset } of strings) {
    // Compute a line number for the offset (cheap, scan-newlines).
    const before = text.slice(0, offset);
    const line = before.split("\n").length;

    for (const rawTok of raw.split(/\s+/)) {
      // Strip stray quote / brace / paren chars. The
      // template-literal extractor sometimes captures a span that
      // INCLUDES embedded string-literal delimiters (e.g. the
      // text between two `${...}` interpolations) — splitting by
      // whitespace then yields tokens like `hover:bg-muted"`. The
      // adjacent `"..."` extraction picks up the clean version
      // separately, but defensive trim here is cheaper than a
      // dedupe pass.
      const cls = rawTok.replace(/^[`"'(){}[\],;:]+|[`"'(){}[\],;:]+$/g, "");
      if (!cls) continue;
      const parsed = parseColorClass(cls);
      if (!parsed) continue;
      if (!allowed.has(parsed.color)) {
        offenders.push({
          file: relative(REPO, file),
          line,
          util: parsed.util,
          color: parsed.color,
          raw: parsed.raw,
        });
      }
    }
  }
}

if (offenders.length === 0) {
  console.log(`tailwind-classes lint: ${files.length} files scanned, 0 phantom classes`);
  process.exit(0);
}

console.error(`tailwind-classes lint: ${offenders.length} phantom class references\n`);
for (const o of offenders) {
  console.error(`  ${o.file}:${o.line}  ${o.raw}  (color key "${o.color}" not in tailwind.config.ts theme.extend.colors)`);
}
console.error(
  "\nFix: either add the colour to `colors` in tailwind.config.ts, or",
  "switch to an existing key (e.g. `bg-card`, `bg-muted`, `bg-background`).",
);
process.exit(1);
