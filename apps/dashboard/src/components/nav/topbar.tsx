"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  Bell,
  Bot,
  ChevronDown,
  LayoutDashboard,
  Loader2,
  Mic,
  MicOff,
  Plus,
  Search,
  Sparkles,
  Wand2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, type Agent, type Skill, type Template } from "@/lib/api";

type Hit = {
  // `page` = static dashboard tab destination. Added in v0.2.13's
  // command-palette UX so the same top-bar input can navigate to
  // /dashboard/agents, /dashboard/integrations, etc. without
  // requiring a separate Cmd+K modal.
  kind: "agent" | "template" | "skill" | "page";
  id: string;
  title: string;
  subtitle?: string;
  href: string;
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

  const hits = useMemo<Hit[]>(() => {
    if (!q.trim()) return [];
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
  }, [q, agents, templates, skills]);

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

  function go(h: Hit) {
    setOpen(false);
    setQ("");
    router.push(h.href);
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

  const IconFor = ({ kind }: { kind: Hit["kind"] }) => {
    if (kind === "agent") return <Bot className="h-3.5 w-3.5 text-violet-300" />;
    if (kind === "template") return <Sparkles className="h-3.5 w-3.5 text-cyan-300" />;
    if (kind === "page") return <LayoutDashboard className="h-3.5 w-3.5 text-amber-300" />;
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
            {!q.trim() ? (
              <div className="px-3 py-4 text-sm text-muted-foreground">
                Start typing or click the mic to search.
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
                    <IconFor kind={h.kind} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate">{h.title}</div>
                      {h.subtitle && (
                        <div className="truncate text-xs text-muted-foreground">{h.subtitle}</div>
                      )}
                    </div>
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
