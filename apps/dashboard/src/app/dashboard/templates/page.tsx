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
  Calendar,
  FileText,
  PhoneOutgoing,
  Languages,
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
  Calendar,
  FileText,
  PhoneOutgoing,
  Languages,
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

  async function instantiate(t: Template) {
    const existing = agents.filter((a) => a.template_id === t.id);
    if (existing.length > 0) {
      const names = existing.map((a) => `• ${a.name}`).join("\n");
      const msg =
        `You already have ${existing.length} agent${existing.length === 1 ? "" : "s"} ` +
        `from the “${t.name}” template:\n\n${names}\n\n` +
        `OK = open the existing one. Cancel = create another copy.`;
      if (confirm(msg)) {
        router.push(`/dashboard/agents/${existing[0].id}`);
        return;
      }
      // Falls through to create a fresh copy on Cancel.
    }
    setBusy(t.id);
    try {
      const a = await api.instantiateTemplate(t.id);
      router.push(`/dashboard/agents/${a.id}`);
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
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {templates.map((t) => {
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
                      Use template
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
