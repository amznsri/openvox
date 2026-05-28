"use client";

import useSWR from "swr";
import { CheckCircle2, Circle, Loader2, Plug } from "lucide-react";

import { api, type Provider } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const TYPE_ORDER = ["llm", "stt", "tts", "rtc"] as const;
const TYPE_NAMES: Record<string, string> = {
  llm: "Language models",
  stt: "Speech-to-text",
  tts: "Text-to-speech",
  rtc: "Realtime communication",
  vad: "Voice activity detection",
  s2s: "Speech-to-speech",
  translate: "Live interpretation",
};

export default function ProvidersPage() {
  const { data: providers = [], isLoading } = useSWR<Provider[]>("providers", () =>
    api.listProviders(),
  );

  const groups: Record<string, Provider[]> = {};
  for (const p of providers) {
    (groups[p.type] = groups[p.type] || []).push(p);
  }

  return (
    <div className="container py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Providers</h1>
        <p className="text-muted-foreground text-sm">
          Every provider is configurable via your <code className="text-foreground">.env</code>.
          Greyed-out cards have no API key set.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : (
        TYPE_ORDER.map((t) => {
          const list = groups[t] || [];
          if (list.length === 0) return null;
          return (
            <Card key={t}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Plug className="h-4 w-4 text-cyan-300" />
                  {TYPE_NAMES[t] ?? t}
                  <Badge variant="default" className="ml-1">
                    {list.filter((p) => p.available).length} / {list.length} ready
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {list.map((p) => (
                  <div
                    key={`${p.type}.${p.id}`}
                    className={`px-4 py-3 rounded-lg border transition-colors ${
                      p.available
                        ? "border-border/60 bg-card/40 hover:border-primary/40"
                        : "border-border/40 bg-card/20 opacity-60"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="font-medium text-sm">{p.display_name}</div>
                      {p.available ? (
                        <CheckCircle2 className="h-4 w-4 text-success" />
                      ) : (
                        <Circle className="h-4 w-4 text-muted-foreground" />
                      )}
                    </div>
                    <div className="font-mono text-[11px] text-muted-foreground mb-2">{p.id}</div>
                    <div className="flex flex-wrap gap-1">
                      {p.capabilities.map((c) => (
                        <Badge key={c} variant="default" className="text-[10px]">
                          {c}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          );
        })
      )}

      {/* Roadmap providers */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-amber-300" />
            Roadmap
            <Badge variant="warning">beta</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {[
            // Card list mirrors what's actually wired vs what's still
            // aspirational. Edit honestly — every "works today" line
            // here is a promise we have to be able to back up in code.
            { name: "BytePlus VAD", desc: "Voice activity detection (placeholder)" },
            { name: "Silero VAD", desc: "Self-hosted VAD model — works today" },
            { name: "BytePlus S2S", desc: "Speech-to-speech (placeholder)" },
            { name: "OpenAI Realtime", desc: "Speech-to-speech (planned — not yet wired)" },
            { name: "BytePlus Translate", desc: "Live interpretation (placeholder)" },
            { name: "BytePlus Podcast", desc: "Two-speaker podcast generation (placeholder)" },
          ].map((p) => (
            <div key={p.name} className="px-4 py-3 rounded-lg border border-dashed border-border/60 bg-card/20">
              <div className="font-medium text-sm">{p.name}</div>
              <div className="text-xs text-muted-foreground mt-1">{p.desc}</div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
