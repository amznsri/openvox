"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  Bell,
  Bot,
  ChevronDown,
  HelpCircle,
  LayoutDashboard,
  Loader2,
  Mic,
  MicOff,
  Play,
  Plus,
  Plug,
  Search,
  Sparkles,
  Wand2,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, type Agent, type Skill, type Template } from "@/lib/api";
import {
  HELP_SECTIONS,
  parseActionCommand,
  type ActionMatch,
} from "@/lib/command-actions";

type Hit = {
  // `page`   = static dashboard tab destination (v0.2.13).
  // `action` = verb-driven command like "test <agent>" or
  //            "create from template <X>" (v0.2.14 / Tier 2).
  kind: "agent" | "template" | "skill" | "page" | "action";
  id: string;
  title: string;
  subtitle?: string;
  /** Pure-navigation hits set href. Action hits set `run` instead
   *  (e.g. instantiate-then-redirect). Exactly one of the two is
   *  present per Hit. */
  href?: string;
  run?: () => Promise<void> | void;
  /** Action hits use this glyph so the renderer can distinguish
   *  test-vs-create-vs-connect-vs-disconnect-vs-help without
   *  reparsing the title. */
  actionKind?: "test" | "create" | "connect" | "disconnect" | "help";
};


/** Dashboard pages — every primary route reachable from the sidebar
 *  plus a couple of high-value "create" deeplinks. The `keywords`
 *  field gives the fuzzy scorer extra anchors so "create" finds the
 *  New Agent route even though the title is just "New agent". */
const PAGES: { id: string; title: string; subtitle: string; href: string; keywords: string }[] = [
  { id: "overview",      title: "Overview",       subtitle: "Dashboard home",                       href: "/dashboard",                 keywords: "home start" },
  { id: "playground",    title: "Playground",     subtitle: "Voice / text / audio / docs sandbox",  href: "/dashboard/playground",      keywords: "test chat try" },
  { id: "agents",        title: "Agents",         subtitle: "List + edit your voice agents",        href: "/dashboard/agents",          keywords: "bots" },
  { id: "agents-new",    title: "New agent",      subtitle: "Create an agent (voice or form)",      href: "/dashboard/agents/new",      keywords: "create build new" },
  { id: "templates",     title: "Templates",      subtitle: "Pre-built agents you can instantiate", href: "/dashboard/templates",       keywords: "starter starters preset" },
  { id: "skills",        title: "Skills",         subtitle: "Tools the LLM can call",               href: "/dashboard/skills",          keywords: "tools functions" },
  { id: "schedules",     title: "Schedules",      subtitle: "Cron + interval + webhook jobs",       href: "/dashboard/schedules",       keywords: "cron jobs tasks recurring" },
  { id: "evals",         title: "Evals",          subtitle: "Eval runs + personas + recordings",    href: "/dashboard/evals",           keywords: "tests quality grading" },
  { id: "providers",     title: "Providers",      subtitle: "STT / TTS / LLM / RTC backends",       href: "/dashboard/providers",       keywords: "byteplus openai elevenlabs deepgram" },
  { id: "integrations",  title: "Integrations",   subtitle: "Connect Gmail / Calendar / Contacts",  href: "/dashboard/integrations",    keywords: "google connect oauth" },
  { id: "observability", title: "Observability",  subtitle: "Session history + transcripts",        href: "/dashboard/observability",   keywords: "logs sessions metrics" },
  { id: "settings",      title: "Settings",       subtitle: "Provider keys + global config",        href: "/dashboard/settings",        keywords: "config preferences" },
];

/** Tiny case-insensitive matcher — substring + word-start ranks higher than
 * arbitrary middle-of-word matches so "rec" finds "Receptionist" first. */
function score(needle: string, hay: string): number {
  if (!needle) return 0;
  const h = hay.toLowerCase();
  const n = needle.toLowerCase();
  if (!h.includes(n)) return 0;
  let s = 1;
  if (h.startsWith(n)) s += 4;
  if (new RegExp(`\\b${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(h)) s += 2;
  return s;
}

export function Topbar({ title }: { title?: string }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ── Voice (Web Speech API, push-to-talk) ─────────────────────
  //
  // Why Web Speech API rather than routing through our own STT
  // provider:
  //   - Zero added latency (in-browser; no upload to the daemon).
  //   - Zero cost on the OpenVox side — the browser handles the
  //     recogniser. The trade-off (cloud round-trip on Chrome /
  //     Edge, local on Safari) is surfaced honestly in the mic
  //     tooltip below.
  //   - Already used elsewhere in this codebase for the stop-word
  //     barge-in path during TTS playback (CLAUDE.md §8 #64).
  //
  // Why push-to-talk only (not a wake-word):
  //   Continuous mic + recogniser is the bug class CLAUDE.md §8 #52
  //   warned about. Demands explicit user intent each turn.
  const [listening, setListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const recogRef = useRef<any>(null);

  useEffect(() => {
    // Feature-detect on mount. Firefox doesn't ship SpeechRecognition
    // at all — the mic button stays hidden there rather than mocked
    // (a dead button would be worse UX than no button).
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    setVoiceSupported(typeof SR === "function");
  }, []);

  function startVoice() {
    setVoiceError(null);
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!SR) {
      setVoiceError("This browser doesn't support speech recognition.");
      return;
    }
    const recog = new SR();
    recog.lang = "en-US";
    recog.interimResults = true;
    recog.continuous = false;       // stop on first final result
    recog.maxAlternatives = 1;
    recog.onresult = (event: any) => {
      // Pull the latest result + show it in the input as the user
      // speaks. Final result populates the query + opens the popover.
      let transcript = "";
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      setQ(transcript.trim());
      setOpen(true);
    };
    recog.onerror = (event: any) => {
      // `not-allowed` = user denied mic permission; `no-speech` = silence.
      const tag = event?.error || "unknown";
      setVoiceError(
        tag === "not-allowed"
          ? "Microphone permission denied. Enable it in your browser settings."
          : tag === "no-speech"
            ? "Didn't catch that — try again."
            : `Voice input failed (${tag}).`,
      );
      setListening(false);
    };
    recog.onend = () => {
      setListening(false);
      // Refocus the text input so the user can refine the query
      // immediately if the transcript needs editing.
      inputRef.current?.focus();
    };
    recogRef.current = recog;
    try {
      recog.start();
      setListening(true);
      setOpen(true);
    } catch (e) {
      // Safari throws if start() is called too quickly after a
      // previous session ended — surface a friendly retry hint.
      setVoiceError("Couldn't start mic — try again in a moment.");
      setListening(false);
    }
  }

  function stopVoice() {
    try {
      recogRef.current?.stop();
    } catch {
      // ignore
    }
    setListening(false);
  }

  // ── Cmd+K / Ctrl+K global listener ───────────────────────────
  //
  // Convention from Linear / Vercel / Notion. Browsers' default
  // Cmd+K is "focus address bar with search engine" on Chrome — we
  // override with `preventDefault()` while the dashboard is the
  // focused tab. macOS users hit Cmd+K, everyone else hits Ctrl+K.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isModK = (e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K");
      if (!isModK) return;
      // Avoid stealing the key from a text-input that already has
      // focus and might use Cmd+K for its own purposes (e.g. a
      // textarea editor). Only fire when the dashboard's own
      // search isn't already focused (in which case the user just
      // wants to clear/refocus, which Cmd+K still does fine).
      e.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
      setOpen(q.trim().length > 0);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [q]);

  // Pull the three searchable corpora. SWR de-dupes between pages.
  const { data: agents = [] } = useSWR<Agent[]>("agents", () => api.listAgents());
  const { data: templates = [] } = useSWR<Template[]>("templates", () => api.listTemplates());
  const { data: skills = [] } = useSWR<Skill[]>("skills", () => api.listSkills());

  // Parse the query as a Tier-2 action command. When this returns a
  // match, the hits list switches into "action mode" — fuzzy-resolves
  // the arg against agents / templates as appropriate, surfaces those
  // as RUNNABLE hits (h.run instead of h.href). Tier 1 nav results
  // are suppressed in action mode to keep focus.
  const actionMatch: ActionMatch | null = useMemo(
    () => parseActionCommand(q),
    [q],
  );

  const hits = useMemo<Hit[]>(() => {
    if (!q.trim()) return [];

    // ── Action mode ─────────────────────────────────────────────
    if (actionMatch) {
      // Help is rendered separately in the popover — not via the
      // hit list — so emit zero hits here.
      if (actionMatch.kind === "help") return [];

      if (actionMatch.kind === "connect_gmail") {
        return [{
          kind: "action",
          actionKind: "connect",
          id: "connect-gmail",
          title: "Connect Gmail",
          subtitle: "Open the Integrations tab",
          href: "/dashboard/integrations",
        }];
      }

      if (actionMatch.kind === "disconnect") {
        const email = actionMatch.arg;
        return [{
          kind: "action",
          actionKind: "disconnect",
          id: `disconnect-${email}`,
          title: `Disconnect ${email}`,
          subtitle: "Open Integrations + focus this account (you confirm)",
          href: `/dashboard/integrations?focus=${encodeURIComponent(email)}`,
        }];
      }

      if (actionMatch.kind === "test_agent") {
        // Fuzzy-resolve the agent name. Up to 5 matches; if none, a
        // single "no match" hint hit (no run callback) lands so the
        // popover doesn't look broken.
        const ranked: { agent: Agent; s: number }[] = [];
        for (const a of agents) {
          const s = Math.max(
            score(actionMatch.arg, a.name),
            score(actionMatch.arg, a.description || ""),
          );
          if (s > 0) ranked.push({ agent: a, s });
        }
        ranked.sort((a, b) => b.s - a.s);
        const top = ranked.slice(0, 5);
        if (top.length === 0) {
          return [{
            kind: "action",
            actionKind: "test",
            id: "test-nomatch",
            title: `No agent matches "${actionMatch.arg}"`,
            subtitle: "Try a different name or open the Agents tab",
            href: "/dashboard/agents",
          }];
        }
        return top.map((m) => ({
          kind: "action",
          actionKind: "test",
          id: `test-${m.agent.id}`,
          title: `Test "${m.agent.name}"`,
          subtitle: "Open in Playground",
          // Playground reads ?agent=<id> in its config panel; existing
          // behavior from Phase 1.6's Setup Assistant deeplink work.
          href: `/dashboard/playground?agent=${m.agent.id}`,
        }));
      }

      if (actionMatch.kind === "create_from_template") {
        const ranked: { tpl: Template; s: number }[] = [];
        for (const t of templates) {
          const s = Math.max(
            score(actionMatch.arg, t.name),
            score(actionMatch.arg, t.tagline || ""),
            score(actionMatch.arg, t.category || ""),
          );
          if (s > 0) ranked.push({ tpl: t, s });
        }
        ranked.sort((a, b) => b.s - a.s);
        const top = ranked.slice(0, 5);
        if (top.length === 0) {
          return [{
            kind: "action",
            actionKind: "create",
            id: "create-nomatch",
            title: `No template matches "${actionMatch.arg}"`,
            subtitle: "Try a different name or open the Templates tab",
            href: "/dashboard/templates",
          }];
        }
        return top.map((m) => ({
          kind: "action",
          actionKind: "create",
          id: `create-${m.tpl.id}`,
          title: `Create from "${m.tpl.name}"`,
          subtitle: m.tpl.tagline || "Instantiate this template",
          // Action runs API + redirects — see go(h) handler.
          run: async () => {
            const agent = await api.instantiateTemplate(m.tpl.id);
            router.push(`/dashboard/agents/edit?id=${agent.id}`);
          },
        }));
      }
    }

    // ── Tier 1 — pages + agents + templates + skills ────────────
    const out: { hit: Hit; s: number }[] = [];
    // Pages first — a single-word query like "agents" should jump to
    // the Agents tab rather than fuzzy-matching some agent named
    // something similar. +1.0 boost on the page score handles that.
    for (const p of PAGES) {
      const s = Math.max(
        score(q, p.title),
        score(q, p.subtitle),
        score(q, p.keywords),
      );
      if (s > 0)
        out.push({
          s: s + 1.0,
          hit: { kind: "page", id: p.id, title: p.title, subtitle: p.subtitle, href: p.href },
        });
    }
    for (const a of agents) {
      const s = Math.max(score(q, a.name), score(q, a.description || ""));
      if (s > 0)
        out.push({
          s,
          hit: { kind: "agent", id: a.id, title: a.name, subtitle: a.description || a.llm_provider, href: `/dashboard/agents/edit?id=${a.id}` },
        });
    }
    for (const t of templates) {
      const s = Math.max(score(q, t.name), score(q, t.tagline || ""), score(q, t.category || ""));
      if (s > 0)
        out.push({
          s: s - 0.1, // tiny tiebreak: prefer agents over templates
          hit: { kind: "template", id: t.id, title: t.name, subtitle: t.tagline, href: "/dashboard/templates" },
        });
    }
    for (const sk of skills) {
      const s = Math.max(score(q, sk.id), score(q, sk.display_name || ""), score(q, sk.description || ""));
      if (s > 0)
        out.push({
          s: s - 0.2,
          hit: { kind: "skill", id: sk.id, title: sk.display_name || sk.id, subtitle: sk.id, href: "/dashboard/skills" },
        });
    }
    out.sort((a, b) => b.s - a.s);
    return out.slice(0, 12).map((x) => x.hit);
  }, [q, agents, templates, skills, actionMatch, router]);

  // Close the popover when clicking outside.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Reset highlight when the result list changes.
  useEffect(() => setActiveIdx(0), [hits.length, q]);

  // Tier 2 — action hits may run async work (e.g. instantiate a
  // template + redirect). Show a spinner inline while it's pending
  // so the user sees something happen + can't double-fire by hitting
  // Enter twice. Errors land in `actionError` for inline display.
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function go(h: Hit) {
    setActionError(null);
    if (h.run) {
      setActionBusy(true);
      try {
        await h.run();
      } catch (e: any) {
        setActionError(String(e?.message || e));
        setActionBusy(false);
        return;       // keep popover open so the user sees the error
      }
      setActionBusy(false);
    } else if (h.href) {
      router.push(h.href);
    }
    setOpen(false);
    setQ("");
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || hits.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % hits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + hits.length) % hits.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(hits[activeIdx]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const IconFor = ({ hit }: { hit: Hit }) => {
    if (hit.kind === "agent") return <Bot className="h-3.5 w-3.5 text-violet-300" />;
    if (hit.kind === "template") return <Sparkles className="h-3.5 w-3.5 text-cyan-300" />;
    if (hit.kind === "page") return <LayoutDashboard className="h-3.5 w-3.5 text-amber-300" />;
    if (hit.kind === "skill") return <Wand2 className="h-3.5 w-3.5 text-emerald-300" />;
    // Action hits — distinct icon per action so the renderer
    // distinguishes test (Play) vs create (Plus) vs connect (Plug)
    // vs disconnect (Plug-with-line-through is too niche, reuse Plug
    // but use a warning hue) vs help (HelpCircle).
    if (hit.kind === "action") {
      if (hit.actionKind === "test") return <Play className="h-3.5 w-3.5 text-rose-300" />;
      if (hit.actionKind === "create") return <Plus className="h-3.5 w-3.5 text-rose-300" />;
      if (hit.actionKind === "connect") return <Plug className="h-3.5 w-3.5 text-rose-300" />;
      if (hit.actionKind === "disconnect") return <Plug className="h-3.5 w-3.5 text-amber-300" />;
      if (hit.actionKind === "help") return <HelpCircle className="h-3.5 w-3.5 text-rose-300" />;
      return <Zap className="h-3.5 w-3.5 text-rose-300" />;
    }
    return <Wand2 className="h-3.5 w-3.5 text-emerald-300" />;
  };

  return (
    <header className="h-16 flex items-center justify-between gap-3 px-6 border-b border-border/60 bg-background/40 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        {title && <h1 className="text-base font-semibold">{title}</h1>}
      </div>
      <div ref={wrapRef} className="flex-1 max-w-md mx-auto hidden md:block relative">
        <div className="relative">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOpen(true);
            }}
            onFocus={() => q && setOpen(true)}
            onKeyDown={onKey}
            placeholder="Search or speak — pages, agents, templates, skills"
            // Right-pad enough room for the mic button + Cmd+K badge
            // when both are visible. Without voice support (Firefox)
            // only the kbd badge sits there, so the padding's still
            // fine — it just leaves a bit of dead air.
            className="w-full h-9 rounded-md bg-input/40 border border-border/60 pl-9 pr-20 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          />
          {/* Right-edge cluster: mic toggle + Cmd+K hint. */}
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
            {voiceSupported && (
              <button
                type="button"
                onClick={() => (listening ? stopVoice() : startVoice())}
                aria-label={listening ? "Stop voice input" : "Start voice input"}
                title={
                  listening
                    ? "Listening — click to stop"
                    : "Voice search (uses your browser's speech recogniser)"
                }
                className={`h-7 w-7 inline-flex items-center justify-center rounded-md transition-colors ${
                  listening
                    ? "text-rose-300 bg-rose-500/10 animate-pulse"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
                }`}
              >
                {listening ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
              </button>
            )}
            <kbd className="hidden lg:inline-flex h-5 px-1.5 items-center rounded border border-border/60 bg-muted/40 text-[10px] text-muted-foreground font-mono">
              ⌘K
            </kbd>
          </div>
        </div>
        {(open && q.trim()) || listening || voiceError ? (
          <div className="absolute left-0 right-0 mt-2 rounded-lg border border-border/60 bg-popover/95 backdrop-blur-xl shadow-xl z-50 overflow-hidden">
            {/* Voice-state strip — only shows when listening / errored.
                Honest about where the audio actually goes — Chrome /
                Edge upload to a cloud recogniser, Safari is local. */}
            {(listening || voiceError) && (
              <div className="px-3 py-2 text-xs border-b border-border/40 flex items-center gap-2">
                {listening && (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin text-rose-300" />
                    <span className="text-rose-300">Listening… speak now</span>
                    <span className="text-muted-foreground ml-auto">
                      Uses your browser's built-in speech service
                    </span>
                  </>
                )}
                {!listening && voiceError && (
                  <span className="text-amber-300">{voiceError}</span>
                )}
              </div>
            )}
            {actionError && (
              // Action runs land their errors here so the popover
              // stays open + the user sees what failed (e.g. the
              // /api/v1/templates/<id>/instantiate request errored).
              <div className="px-3 py-2 text-xs border-b border-border/40 text-rose-300">
                {actionError}
              </div>
            )}
            {!q.trim() ? (
              <div className="px-3 py-4 text-sm text-muted-foreground">
                Start typing or click the mic to search. Type{" "}
                <code className="px-1 py-0.5 rounded bg-muted/60 text-foreground text-[11px]">
                  help
                </code>{" "}
                for the command list.
              </div>
            ) : actionMatch?.kind === "help" ? (
              // Help mode — render the command reference instead of
              // a hit list. Pure read-only; closes on Esc or outside-
              // click via the existing handlers.
              <div className="max-h-96 overflow-y-auto p-3 space-y-3">
                {HELP_SECTIONS.map((sec) => (
                  <div key={sec.title}>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
                      {sec.title}
                    </div>
                    <ul className="space-y-1">
                      {sec.items.map((item, i) => (
                        <li
                          key={i}
                          className="text-xs text-foreground/85 font-mono whitespace-pre"
                        >
                          {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            ) : hits.length === 0 ? (
              <div className="px-3 py-4 text-sm text-muted-foreground">
                No matches for “{q}”
              </div>
            ) : (
              <ul className="max-h-96 overflow-y-auto">
                {hits.map((h, i) => (
                  <li
                    key={`${h.kind}-${h.id}`}
                    onMouseEnter={() => setActiveIdx(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      go(h);
                    }}
                    className={`flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer ${
                      i === activeIdx ? "bg-muted/60" : "hover:bg-muted/40"
                    }`}
                  >
                    <IconFor hit={h} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate">{h.title}</div>
                      {h.subtitle && (
                        <div className="truncate text-xs text-muted-foreground">{h.subtitle}</div>
                      )}
                    </div>
                    {actionBusy && i === activeIdx && (
                      // Spinner shown next to the active action hit
                      // while h.run is in-flight (e.g. instantiate
                      // template).
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    )}
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {h.kind}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <Link href="/dashboard/agents/new">
          <Button variant="gradient" size="sm">
            <Plus className="h-4 w-4" />
            New agent
          </Button>
        </Link>
        <Button variant="ghost" size="icon" aria-label="Notifications">
          <Bell className="h-4 w-4" />
        </Button>
        <button className="flex items-center gap-2 h-9 px-2 rounded-md hover:bg-muted">
          <div className="h-7 w-7 rounded-full bg-gradient-to-br from-violet-500 to-cyan-400" />
          <span className="text-sm hidden md:inline">Local user</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      </div>
    </header>
  );
}
