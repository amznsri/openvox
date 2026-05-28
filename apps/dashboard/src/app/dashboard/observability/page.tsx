"use client";

import { useState } from "react";
import useSWR from "swr";
import { BarChart3, Bookmark, ChevronRight, Clock, DollarSign, Loader2, MessageSquare, Phone, X } from "lucide-react";

import { api, type Session, type SessionPricing, type PricingRates } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDate, formatDuration } from "@/lib/utils";

export default function ObservabilityPage() {
  const { data: sessions = [], isLoading } = useSWR<Session[]>("sessions", () =>
    api.listSessions(),
  );
  const [openSessionId, setOpenSessionId] = useState<string | null>(null);

  const total = sessions.length;
  const totalMs = sessions.reduce((acc, s) => acc + s.duration_ms, 0);
  const totalCost = sessions.reduce((acc, s) => acc + s.cost_usd, 0);
  const avgFirstToken = total
    ? Math.round(sessions.reduce((acc, s) => acc + s.first_token_ms, 0) / total)
    : 0;

  const stats = [
    { label: "Sessions", value: total, icon: MessageSquare, color: "text-violet-300" },
    { label: "Talk time", value: formatDuration(totalMs), icon: Clock, color: "text-cyan-300" },
    { label: "Avg first-token", value: `${avgFirstToken} ms`, icon: BarChart3, color: "text-amber-300" },
    { label: "Spend (USD)", value: `$${totalCost.toFixed(2)}`, icon: DollarSign, color: "text-emerald-300" },
  ];

  return (
    <div className="container py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Observability</h1>
        <p className="text-muted-foreground text-sm">
          Per-session metrics, transcripts, and replays.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardContent className="pt-6">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground uppercase tracking-wider">
                  {s.label}
                </span>
                <s.icon className={`h-4 w-4 ${s.color}`} />
              </div>
              <div className="mt-2 text-2xl font-bold tabular-nums">{s.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent sessions</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              No sessions yet. Open the playground or take a phone call to get started.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs uppercase tracking-wider text-muted-foreground border-b border-border/40">
                    <th className="text-left py-2 px-3">Session</th>
                    <th className="text-left py-2 px-3">Channel</th>
                    <th className="text-left py-2 px-3">Started</th>
                    <th className="text-left py-2 px-3">Duration</th>
                    <th className="text-left py-2 px-3">Turns</th>
                    <th className="text-left py-2 px-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr
                      key={s.id}
                      onClick={() => setOpenSessionId(s.id)}
                      className="border-b border-border/40 hover:bg-muted/30 transition-colors cursor-pointer"
                    >
                      <td className="py-2 px-3 font-mono text-xs">{s.id.slice(0, 8)}…</td>
                      <td className="py-2 px-3">
                        <span className="inline-flex items-center gap-1.5">
                          {s.channel === "phone" ? (
                            <Phone className="h-3.5 w-3.5" />
                          ) : (
                            <MessageSquare className="h-3.5 w-3.5" />
                          )}
                          {s.channel}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-muted-foreground">{formatDate(s.started_at)}</td>
                      <td className="py-2 px-3 tabular-nums">{formatDuration(s.duration_ms)}</td>
                      <td className="py-2 px-3 tabular-nums">{s.turn_count}</td>
                      <td className="py-2 px-3">
                        <Badge variant={s.status === "active" ? "warning" : "success"}>
                          {s.status}
                        </Badge>
                        <ChevronRight className="inline h-3.5 w-3.5 text-muted-foreground ml-1" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {openSessionId && (
        <SessionDetailDrawer
          sessionId={openSessionId}
          onClose={() => setOpenSessionId(null)}
        />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// SessionDetailDrawer — slide-in panel with pricing breakdown +
// "save as recording" action for the eval framework.
// ──────────────────────────────────────────────────────────────────────

function SessionDetailDrawer({
  sessionId,
  onClose,
}: {
  sessionId: string;
  onClose: () => void;
}) {
  const { data: pricing, isLoading } = useSWR<SessionPricing>(
    sessionId ? `pricing-${sessionId}` : null,
    () => api.sessionPricing(sessionId),
    { revalidateOnFocus: false },
  );
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState("");

  async function save() {
    setSaving(true);
    setSaveError("");
    try {
      const rec = await api.saveSessionAsRecording(sessionId);
      setSaved(rec.id);
    } catch (e: any) {
      setSaveError(e?.message || "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-stretch justify-end"
      onClick={onClose}
    >
      <div
        className="bg-background border-l border-border/60 w-full max-w-2xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-border/60 flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold">Session detail</h3>
            <p className="text-xs text-muted-foreground font-mono mt-0.5">{sessionId}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="p-5 space-y-5">
          {isLoading || !pricing ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : (
            <>
              <PricingBreakdown pricing={pricing} />

              {/* Eval framework hook: save the session as a Recording
                  so it can be replayed against alternative agent configs. */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Bookmark className="h-4 w-4 text-violet-300" />
                    Save as eval recording
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground mb-3">
                    Promote this session into a reusable test fixture. The
                    Evals page can replay it against new agent configs and
                    measure regressions.
                  </p>
                  {saved ? (
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-200 text-sm px-3 py-2">
                      ✓ Saved as recording <span className="font-mono">{saved.slice(0, 8)}…</span>{" "}
                      — visit the Evals page to use it.
                    </div>
                  ) : (
                    <Button variant="gradient" size="sm" onClick={save} disabled={saving}>
                      {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bookmark className="h-3.5 w-3.5" />}
                      Save as recording
                    </Button>
                  )}
                  {saveError && (
                    <p className="mt-2 text-xs text-rose-300">{saveError}</p>
                  )}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// PricingBreakdown — stacked component cost + what-if matrix.
// ──────────────────────────────────────────────────────────────────────

function PricingBreakdown({ pricing }: { pricing: SessionPricing }) {
  const { actual, alternatives, cheapest, savings_vs_cheapest_usd, telemetry } = pricing;
  const components = actual.components;
  const total = actual.total_usd || 0.000001;
  // Fetch rate card so we can label each component pill with its
  // unit + cited source. SWR caches the response so all open drawers
  // share one fetch.
  const { data: rates } = useSWR<PricingRates>("pricing-rates", () => api.pricingRates());
  // Parse "stt / llm / tts" → resolved ProviderRate per role.
  const [sttId, llmId, ttsId] = actual.rate_card.split(" / ").map((s) => s.trim());
  const sttR = rates?.providers[sttId];
  const llmR = rates?.providers[llmId];
  const ttsR = rates?.providers[ttsId];
  // Per-component unit hint. STT can be per-min OR per-char depending
  // on provider — pick whichever rate the provider actually charges on.
  const sttUnit = sttR
    ? sttR.stt_usd_per_1m_chars > 0
      ? `$${sttR.stt_usd_per_1m_chars.toFixed(2)} / 1M chars`
      : sttR.stt_usd_per_minute > 0
        ? `$${sttR.stt_usd_per_minute.toFixed(4)} / min`
        : ""
    : "";
  const llmInUnit = llmR ? `$${llmR.llm_usd_per_1m_input.toFixed(2)} / 1M tokens` : "";
  const llmOutUnit = llmR ? `$${llmR.llm_usd_per_1m_output.toFixed(2)} / 1M tokens` : "";
  const ttsUnit = ttsR ? `$${ttsR.tts_usd_per_1k_chars.toFixed(3)} / 1k chars` : "";
  // Build a stacked-bar from the four components (avoid divide-by-zero).
  const bars = [
    { key: "stt", label: "STT", value: components.stt, color: "bg-cyan-500", unit: sttUnit, model: sttR?.model_name },
    { key: "llm_input", label: "LLM in", value: components.llm_input, color: "bg-violet-500", unit: llmInUnit, model: llmR?.model_name },
    { key: "llm_output", label: "LLM out", value: components.llm_output, color: "bg-fuchsia-500", unit: llmOutUnit, model: llmR?.model_name },
    { key: "tts", label: "TTS", value: components.tts, color: "bg-emerald-500", unit: ttsUnit, model: ttsR?.model_name },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <DollarSign className="h-4 w-4 text-emerald-300" />
          Cost breakdown
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-baseline gap-2">
          <div className="text-3xl font-bold tabular-nums">${actual.total_usd.toFixed(4)}</div>
          <div className="text-xs text-muted-foreground">
            for {formatDuration(pricing.duration_ms)} · {telemetry.tokens_in}↓ / {telemetry.tokens_out}↑ tokens · {telemetry.stt_chars || 0} ASR / {telemetry.tts_chars} TTS chars
          </div>
        </div>
        {telemetry.estimated_from_duration && (
          <div className="text-xs text-amber-300">
            ⚠ Token counts estimated from duration (provider didn&apos;t return usage on this run).
          </div>
        )}
        {telemetry.stt_chars_estimated && (
          <div className="text-xs text-amber-300">
            ⚠ ASR character count proxied from TTS (older session — new voice calls track it directly).
          </div>
        )}

        {/* Stacked bar */}
        <div>
          <div className="h-3 w-full rounded-full overflow-hidden bg-muted/40 flex">
            {bars.map((b) => {
              const pct = (b.value / total) * 100;
              return pct > 0 ? (
                <div key={b.key} className={b.color} style={{ width: `${pct}%` }} title={`${b.label}: $${b.value.toFixed(6)}`} />
              ) : null;
            })}
          </div>
          <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
            {bars.map((b) => (
              <div key={b.key} className="space-y-0.5">
                <div className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${b.color}`} />
                  <span className="text-muted-foreground">{b.label}</span>
                  <span className="ml-auto font-mono tabular-nums">${b.value.toFixed(4)}</span>
                </div>
                {b.unit && (
                  <div className="pl-3.5 text-[10px] text-muted-foreground/70 font-mono leading-tight">
                    {b.unit}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="text-xs text-muted-foreground font-mono">
          Provider combo: <span className="text-foreground/80">{actual.rate_card}</span>
        </div>

        {/* Rate-card transparency expander. Surfaces model_name +
            source_url + verified_at per provider in the active combo
            so users can audit the numbers themselves. */}
        {rates && (sttR || llmR || ttsR) && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              Rate sources
            </summary>
            <div className="mt-2 space-y-1.5 text-[11px]">
              {[
                { label: "STT", r: sttR, id: sttId },
                { label: "LLM", r: llmR, id: llmId },
                { label: "TTS", r: ttsR, id: ttsId },
              ].filter((x) => x.r).map(({ label, r, id }) => (
                <div key={label} className="flex items-baseline gap-2">
                  <span className="text-muted-foreground w-10 shrink-0">{label}</span>
                  <span className="font-mono">{id}</span>
                  <span className="text-muted-foreground">·</span>
                  <span className="text-foreground/80 truncate">{r!.model_name}</span>
                  {r!.source_url && (
                    <a
                      href={r!.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-auto text-cyan-300 hover:underline shrink-0"
                    >
                      source ↗
                    </a>
                  )}
                  <span
                    className={`shrink-0 text-[10px] ${r!.verified_at ? "text-muted-foreground" : "text-amber-300"}`}
                    title={r!.notes}
                  >
                    {r!.verified_at || "unverified"}
                  </span>
                </div>
              ))}
            </div>
          </details>
        )}

        {/* What-if matrix */}
        {cheapest && savings_vs_cheapest_usd > 0 && (
          <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm">
            💡 Switch to{" "}
            <span className="font-mono text-emerald-300">
              {cheapest.combo.stt} / {cheapest.combo.llm} / {cheapest.combo.tts}
            </span>{" "}
            to save <span className="font-bold">${savings_vs_cheapest_usd.toFixed(4)}</span> per session
            ({((savings_vs_cheapest_usd / actual.total_usd) * 100).toFixed(0)}% reduction).
          </div>
        )}

        {alternatives.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              Show all {alternatives.length} provider combinations
            </summary>
            <div className="mt-2 max-h-64 overflow-y-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-muted-foreground border-b border-border/40">
                    <th className="text-left py-1.5">STT</th>
                    <th className="text-left">LLM</th>
                    <th className="text-left">TTS</th>
                    <th className="text-right">Total</th>
                    <th className="text-right">Δ vs current</th>
                  </tr>
                </thead>
                <tbody>
                  {alternatives.map((alt, i) => (
                    <tr key={i} className="border-b border-border/20">
                      <td className="py-1 font-mono">{alt.combo.stt}</td>
                      <td className="font-mono">{alt.combo.llm}</td>
                      <td className="font-mono">{alt.combo.tts}</td>
                      <td className="text-right tabular-nums">${alt.total_usd.toFixed(4)}</td>
                      <td className={`text-right tabular-nums ${alt.delta_usd < 0 ? "text-emerald-300" : "text-rose-300"}`}>
                        {alt.delta_usd >= 0 ? "+" : ""}${alt.delta_usd.toFixed(4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}

        {actual.warnings.length > 0 && (
          <div className="text-xs text-amber-300 space-y-0.5">
            {actual.warnings.map((w, i) => (
              <div key={i}>⚠ {w}</div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
