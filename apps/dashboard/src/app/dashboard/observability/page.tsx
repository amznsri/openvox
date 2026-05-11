"use client";

import useSWR from "swr";
import { BarChart3, Clock, DollarSign, Loader2, MessageSquare, Phone } from "lucide-react";

import { api, type Session } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDate, formatDuration } from "@/lib/utils";

export default function ObservabilityPage() {
  const { data: sessions = [], isLoading } = useSWR<Session[]>("sessions", () =>
    api.listSessions(),
  );

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
                    <tr key={s.id} className="border-b border-border/40 hover:bg-muted/30 transition-colors">
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
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
