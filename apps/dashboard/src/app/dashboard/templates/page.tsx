"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import {
  ShoppingBag,
  GraduationCap,
  TrendingUp,
  Mic,
  Sparkles,
  Loader2,
  ArrowRight,
  Briefcase,
  Calendar,
  CalendarPlus,
  FileText,
  Mail,
  PhoneOutgoing,
  Languages,
  Wand2,
} from "lucide-react";

import { api, type Agent, type Template } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const ICONS = {
  ShoppingBag,
  GraduationCap,
  TrendingUp,
  Mic,
  Briefcase,
  Calendar,
  CalendarPlus,
  FileText,
  Mail,
  PhoneOutgoing,
  Languages,
  Wand2,
} as const;

export default function TemplatesPage() {
  const router = useRouter();
  const { data: templates = [], isLoading } = useSWR<Template[]>("templates", () =>
    api.listTemplates(),
  );
  // We need the current agent list to detect "you already have one from this
  // template" — keeps users from accidentally creating Acme Support Voice #4.
  const { data: agents = [] } = useSWR<Agent[]>("agents", () => api.listAgents());
  const [busy, setBusy] = useState<string | null>(null);
  // `null` = "All". Otherwise a BCP-47 short code from the template's
  // `language` field (only set on Session 8 multi-language templates).
  // Setting to "_core" shows just the original 8 (language-agnostic).
  const [langFilter, setLangFilter] = useState<string | null>(null);

  async function instantiate(t: Template) {
    // "Copy template" semantics: every click produces a fresh agent.
    // The backend auto-suffixes duplicate names ("Acme Support Voice (2)",
    // "(3)", …) so the Agents list stays scannable. We deliberately no
    // longer prompt "OK = open existing, Cancel = make another copy" —
    // the button label promises a copy, and existing copies are one
    // click away in the Agents sidebar.
    setBusy(t.id);
    try {
      const a = await api.instantiateTemplate(t.id);
      router.push(`/dashboard/agents/edit?id=${a.id}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="container py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Templates</h1>
        <p className="text-muted-foreground text-sm">
          Production-quality blueprints. One click to instantiate.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : (
        <>
          {/* Language filter chips. We collapse the chip row when only
              the original 8 (no `language` field) are present. */}
          {(() => {
            const langs = Array.from(
              new Set(templates.map((t) => t.language).filter(Boolean) as string[]),
            ).sort();
            if (langs.length === 0) return null;
            const flag: Record<string, string> = {
              en: "🇺🇸", zh: "🇨🇳", yue: "🇭🇰", es: "🇪🇸",
              id: "🇮🇩", fr: "🇫🇷", hi: "🇮🇳",
            };
            const label: Record<string, string> = {
              en: "English", zh: "中文", yue: "粵語", es: "Español",
              id: "Bahasa", fr: "Français", hi: "हिन्दी",
            };
            const chip = (key: string | null, txt: string) => (
              <button
                key={key ?? "__all"}
                onClick={() => setLangFilter(key)}
                className={`px-3 py-1 rounded-full text-xs border transition-colors ${
                  langFilter === key
                    ? "border-violet-400 bg-violet-500/15 text-violet-200"
                    : "border-border/60 bg-muted/30 text-muted-foreground hover:bg-muted/50"
                }`}
              >
                {txt}
              </button>
            );
            return (
              <div className="flex flex-wrap gap-2 pb-2">
                {chip(null, "All")}
                {chip("_core", "Core (no language)")}
                {langs.map((l) => chip(l, `${flag[l] || ""} ${label[l] || l}`))}
              </div>
            );
          })()}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {templates
            .filter((t) => {
              if (langFilter === null) return true;
              if (langFilter === "_core") return !t.language;
              return t.language === langFilter;
            })
            .map((t) => {
            const Icon = (ICONS as any)[t.icon] || Sparkles;
            const existingCount = agents.filter((a) => a.template_id === t.id).length;
            return (
              <Card key={t.id} className="hover:border-primary/40 transition-colors group">
                <CardContent className="pt-6">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-violet-500/20 to-cyan-400/20 flex items-center justify-center">
                        <Icon className="h-5 w-5 text-violet-300" />
                      </div>
                      <div>
                        <h3 className="font-semibold">{t.name}</h3>
                        <div className="mt-1 flex items-center gap-1.5">
                          <Badge variant="default">{t.category}</Badge>
                          {existingCount > 0 && (
                            <Badge variant="success" title="You already have agents from this template">
                              {existingCount} created
                            </Badge>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                  <p className="mt-4 text-sm text-muted-foreground">{t.tagline}</p>
                  <div className="mt-4">
                    <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                      Sample queries
                    </div>
                    <ul className="space-y-1">
                      {t.use_cases.map((u, i) => (
                        <li
                          key={i}
                          className="text-xs px-3 py-2 rounded-md bg-muted/40 border border-border/40 text-foreground/80"
                        >
                          “{u}”
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="mt-5 flex items-center justify-between">
                    <div className="flex flex-wrap gap-1.5">
                      {(t.default.skills || []).map((s) => (
                        <Badge key={s} variant="primary" className="font-mono">
                          {s}
                        </Badge>
                      ))}
                    </div>
                    <Button
                      variant="gradient"
                      size="sm"
                      onClick={() => instantiate(t)}
                      disabled={busy === t.id}
                    >
                      {busy === t.id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowRight className="h-4 w-4" />
                      )}
                      Copy template
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
        </>
      )}
    </div>
  );
}
