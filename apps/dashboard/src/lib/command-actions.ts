/**
 * Action-command parser for the top-bar command palette (Tier 2).
 *
 * Tier 1 (already shipped in v0.2.13) added Cmd+K + fuzzy navigation
 * + voice input. This layer adds VERB-driven actions like:
 *
 *   "test <agent>"              → open Playground with that agent
 *   "create from template <X>"  → instantiate template + open new agent
 *   "connect gmail"             → open Integrations
 *   "disconnect <email>"        → Integrations focused on that account
 *   "help" / "?"                → show the command list
 *
 * Design notes:
 *
 *   - Each command exposes a tiny grammar match. Multiple phrasings
 *     are accepted ("test", "try", "play with") so STT slop doesn't
 *     dead-end the user. Phrasings are case-insensitive + trimmed.
 *
 *   - Argument fuzzy-matching against agent / template lists lives
 *     in the consuming component (topbar.tsx) because that's where
 *     the SWR'd corpora are. This file stays pure (parser only) so
 *     the unit tests don't need to mock the API.
 *
 *   - "Disconnect <email>" deliberately STOPS at the navigation step.
 *     It scrolls + highlights the matching account row but does NOT
 *     auto-click Disconnect. Voice transcripts of email addresses are
 *     too lossy to trust ("john at example dot com" → "[john at
 *     example dot com]"), and disconnecting tokens is a destructive
 *     write. The user makes the final click.
 *
 *   - "Connect Gmail" similarly deeplinks to Integrations rather
 *     than auto-clicking the button. Consent flows + new-window
 *     blockers don't play nice with programmatic clicks; the user
 *     intent + browser permission both want a real click.
 */

export type ActionCommandKind =
  | "open_page"
  | "test_agent"
  | "create_from_template"
  | "connect_gmail"
  | "disconnect"
  | "help";

export type ActionMatch = {
  kind: ActionCommandKind;
  /** Free-text argument extracted from the query (e.g. the agent
   *  name in "test acme support"). Empty for argument-less commands
   *  like "connect gmail" or "help". */
  arg: string;
  /** What the user typed verbatim — useful for the displayed Hit's
   *  title when the arg has trailing whitespace etc. */
  raw: string;
};


/** Each phrasing in this list is a CASE-INSENSITIVE prefix match.
 *  Longer phrasings come first so "create from template foo" wins
 *  over "create foo" (which would otherwise match
 *  `create_from_template` with arg "from template foo"). */
type Phrasing = {
  kind: ActionCommandKind;
  prefix: string;
  /** When true, the arg field MUST be non-empty for the match to
   *  count. "test " with nothing after isn't a usable "test <agent>"
   *  invocation; we'd rather show the user the help list. */
  requiresArg: boolean;
};

const PHRASINGS: Phrasing[] = [
  // Longest first — "create from template X" beats "create X".
  { kind: "create_from_template", prefix: "create from template ", requiresArg: true },
  { kind: "create_from_template", prefix: "new from template ",    requiresArg: true },
  { kind: "create_from_template", prefix: "use template ",          requiresArg: true },
  { kind: "create_from_template", prefix: "instantiate ",           requiresArg: true },

  { kind: "test_agent", prefix: "test ",         requiresArg: true },
  { kind: "test_agent", prefix: "try ",          requiresArg: true },
  { kind: "test_agent", prefix: "play with ",    requiresArg: true },
  { kind: "test_agent", prefix: "playground ",   requiresArg: true },

  { kind: "disconnect", prefix: "disconnect ",   requiresArg: true },

  // Open-page verbs. Multiple phrasings cover the natural English
  // ways a user might say it — "Open agents", "go to evals",
  // "show me playground" etc. The arg (everything after the verb)
  // gets fuzzy-resolved against the PAGES catalog in topbar.tsx.
  // Suffix-strip ("agents page" → "agents") happens in the consumer
  // so the parser stays pure.
  { kind: "open_page", prefix: "open ",           requiresArg: true },
  { kind: "open_page", prefix: "go to ",          requiresArg: true },
  { kind: "open_page", prefix: "navigate to ",    requiresArg: true },
  { kind: "open_page", prefix: "show me ",        requiresArg: true },
  { kind: "open_page", prefix: "show ",           requiresArg: true },
  { kind: "open_page", prefix: "take me to ",     requiresArg: true },

  // Exact-match commands (no argument).
  { kind: "connect_gmail", prefix: "connect gmail",     requiresArg: false },
  { kind: "connect_gmail", prefix: "connect google",    requiresArg: false },
  { kind: "connect_gmail", prefix: "connect calendar",  requiresArg: false },

  { kind: "help", prefix: "help",             requiresArg: false },
  { kind: "help", prefix: "what can i do",    requiresArg: false },
  { kind: "help", prefix: "what can you do",  requiresArg: false },
  { kind: "help", prefix: "?",                requiresArg: false },
  { kind: "help", prefix: "commands",         requiresArg: false },
];


/** Replace intra-phrase punctuation with whitespace and collapse
 *  runs of whitespace. Case-preserving — used for arg extraction.
 *
 *  Browser STT (Chrome / Edge cloud recogniser, Safari local) loves
 *  to insert commas / periods that a typed query wouldn't have. The
 *  user says "create from template Email Assistant"; the recogniser
 *  returns "Create from template, Email Assistant." — a comma after
 *  "template" and a trailing period. Our prefix list expects literal
 *  `"create from template "` (with a SPACE between "template" and
 *  the arg), so the comma kills the prefix match and the whole thing
 *  falls through to Tier 1 fuzzy search. The user sees no "Create
 *  from…" action card and concludes the command is broken.
 *
 *  We treat `[.,;:!?]` as soft delimiters — they collapse to a single
 *  space — so "create from template, email assistant." normalises to
 *  "create from template email assistant" and matches cleanly. The
 *  original `query` is still passed back as `raw` for callers that
 *  want the verbatim text (e.g. Hit titles). */
function normalisePhrase(query: string): string {
  return query
    // Only strip punctuation that's TERMINAL — at end of string or
    // immediately before whitespace. Preserves internal punctuation
    // so email addresses ("alice@example.com"), URLs, and decimals
    // pass through intact. Without this lookahead, "disconnect
    // alice@example.com" would normalise to "disconnect
    // alice@example com" and break the fuzzy match against stored
    // OAuth account emails.
    .replace(/[.,;:!?](?=\s|$)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Try every phrasing in order; return the first match. */
export function parseActionCommand(query: string): ActionMatch | null {
  const raw = query;
  // Bare "?" is the shortest help trigger we accept — and the
  // normaliser would strip it (since it's terminal) leaving an
  // empty string. Special-case it before normalising. Same for
  // multi-punct variants like "??" / "?!" which are still clearly
  // a help request from a confused user.
  const trimmedRaw = query.trim();
  if (/^[?!]+$/.test(trimmedRaw)) return { kind: "help", arg: "", raw };
  // `cased` keeps the user's original case so the arg displays nicely
  // ("ACME" stays "ACME", "Mira" stays "Mira"). `lower` is just for
  // case-insensitive prefix matching — fuzzy `score()` downstream is
  // already case-insensitive, so the arg's case is purely cosmetic.
  const cased = normalisePhrase(query);
  if (!cased) return null;
  const lower = cased.toLowerCase();

  for (const p of PHRASINGS) {
    if (p.requiresArg) {
      if (lower.startsWith(p.prefix)) {
        // Slice the case-preserving form so "test ACME" returns
        // "ACME", not "acme". Use the same offset as the prefix
        // match (lengths match because punctuation→space is 1:1).
        const arg = cased.slice(p.prefix.length).trim();
        if (arg) return { kind: p.kind, arg, raw };
      }
    } else {
      // No-arg commands. "help" exactly, OR "help" + punctuation
      // (which normalisePhrase already stripped). Post-normalise
      // there's no punctuation in `lower`, so an exact-equals check
      // covers "help" / "help." / "help?".
      if (lower === p.prefix) return { kind: p.kind, arg: "", raw };
    }
  }
  return null;
}


/** Strip trailing nav-y words from an "open <X>" argument so the
 *  fuzzy matcher sees a clean page name. Voice transcripts and
 *  natural typing both produce "agents page" / "evals tab" — the
 *  trailing word is noise. Order matters: "section" is checked
 *  AFTER "tab" so "agents tab section" still strips both. */
const STRIPPABLE_SUFFIXES = [
  "page",
  "tab",
  "section",
  "screen",
  "view",
];

export function stripNavSuffix(arg: string): string {
  let s = arg.trim();
  // Strip repeatedly — "agents page tab" → "agents page" → "agents".
  let changed = true;
  while (changed) {
    changed = false;
    for (const sfx of STRIPPABLE_SUFFIXES) {
      const lower = s.toLowerCase();
      if (lower.endsWith(" " + sfx)) {
        s = s.slice(0, -(sfx.length + 1)).trim();
        changed = true;
      } else if (lower === sfx) {
        // Edge case: bare "open page" / "open tab" — arg becomes
        // empty after strip. Caller treats empty arg as "no
        // resolvable target".
        s = "";
        changed = true;
      }
    }
  }
  return s;
}


/** Static help content — rendered when query parses as `help`.
 *  Kept in this file (not topbar.tsx) so it sits next to the
 *  command definitions and stays in sync if a new phrasing is
 *  added. */
export const HELP_SECTIONS: { title: string; items: string[] }[] = [
  {
    title: "Navigation",
    items: [
      "Type a page name: agents, playground, evals, settings, …",
      "Or say it: 'open agents', 'go to evals', 'show me playground'",
      "Cmd+K (Ctrl+K) focuses the search from anywhere",
      "↑↓ to navigate results, Enter to open, Esc to close",
    ],
  },
  {
    title: "Voice",
    items: [
      "Click the mic OR press Cmd+Shift+Space to start listening",
      "Speak a command (try 'open agents' / 'test <agent name>')",
      "After voice navigation, mic stays armed for 8s — say another",
      "  command and the timer resets; silence ends the conversation",
      "Click the mic again to mute immediately",
    ],
  },
  {
    title: "Search",
    items: [
      "Type an agent / template / skill name to find it",
    ],
  },
  {
    title: "Actions",
    items: [
      "test <agent name>           — open in Playground",
      "create from template <name> — instantiate + open the new agent",
      "connect gmail               — open the Integrations tab",
      "disconnect <email>          — focus that account row (no auto-action)",
      "help                        — show this list",
    ],
  },
];
