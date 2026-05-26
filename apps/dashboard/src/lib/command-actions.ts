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


/** Try every phrasing in order; return the first match. */
export function parseActionCommand(query: string): ActionMatch | null {
  const raw = query;
  const lower = query.trim().toLowerCase();
  if (!lower) return null;

  for (const p of PHRASINGS) {
    if (p.requiresArg) {
      if (lower.startsWith(p.prefix)) {
        const arg = query.trim().slice(p.prefix.length).trim();
        if (arg) return { kind: p.kind, arg, raw };
      }
    } else {
      // No-arg commands accept the exact phrasing OR the phrasing
      // followed by punctuation. "help." and "help?" both count.
      if (lower === p.prefix) return { kind: p.kind, arg: "", raw };
      if (lower.startsWith(p.prefix)) {
        const tail = lower.slice(p.prefix.length).trim();
        // Allow nothing or a punctuation-only tail (".", "?", "!").
        if (!tail || /^[.!?]+$/.test(tail)) {
          return { kind: p.kind, arg: "", raw };
        }
      }
    }
  }
  return null;
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
      "Cmd+K (Ctrl+K) focuses the search from anywhere",
      "↑↓ to navigate results, Enter to open, Esc to close",
    ],
  },
  {
    title: "Search",
    items: [
      "Type an agent / template / skill name to find it",
      "Click the mic to dictate via your browser's speech recogniser",
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
