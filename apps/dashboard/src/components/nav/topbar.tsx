"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  Bell,
  Bot,
  ChevronDown,
  Plus,
  Search,
  Sparkles,
  Wand2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, type Agent, type Skill, type Template } from "@/lib/api";

type Hit = {
  kind: "agent" | "template" | "skill";
  id: string;
  title: string;
  subtitle?: string;
  href: string;
};

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

  // Pull the three searchable corpora. SWR de-dupes between pages.
  const { data: agents = [] } = useSWR<Agent[]>("agents", () => api.listAgents());
  const { data: templates = [] } = useSWR<Template[]>("templates", () => api.listTemplates());
  const { data: skills = [] } = useSWR<Skill[]>("skills", () => api.listSkills());

  const hits = useMemo<Hit[]>(() => {
    if (!q.trim()) return [];
    const out: { hit: Hit; s: number }[] = [];
    for (const a of agents) {
      const s = Math.max(score(q, a.name), score(q, a.description || ""));
      if (s > 0)
        out.push({
          s,
          hit: { kind: "agent", id: a.id, title: a.name, subtitle: a.description || a.llm_provider, href: `/dashboard/agents/${a.id}` },
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
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOpen(true);
            }}
            onFocus={() => q && setOpen(true)}
            onKeyDown={onKey}
            placeholder="Search agents, templates, skills…"
            className="w-full h-9 rounded-md bg-input/40 border border-border/60 pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          />
        </div>
        {open && q.trim() && (
          <div className="absolute left-0 right-0 mt-2 rounded-lg border border-border/60 bg-popover/95 backdrop-blur-xl shadow-xl z-50 overflow-hidden">
            {hits.length === 0 ? (
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
        )}
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
