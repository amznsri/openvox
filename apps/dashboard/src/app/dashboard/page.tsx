"use client";

import Link from "next/link";
import useSWR from "swr";
import {
  Activity,
  Bot,
  Plug,
  Sparkles,
  Mic,
  TrendingUp,
  ArrowUpRight,
  Loader2,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, type Agent, type Provider, type Session } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// Synthetic 24-h latency series until real telemetry arrives.
const latencyDemo = Array.from({ length: 24 }, (_, i) => ({
  hour: `${i}:00`,
  ms: 220 + Math.round(Math.sin(i / 3) * 40 + Math.random() * 40),
}));

export default function DashboardOverview() {
  const { data: agents = [], isLoading: aLoading } = useSWR<Agent[]>("agents", () => api.listAgents());
  const { data: providers = [] } = useSWR<Provider[]>("providers", () => api.listProviders());
  const { data: sessions = [] } = useSWR<Session[]>("sessions", () => api.listSessions());

  const availableProviders = providers.filter((p) => p.available).length;
  const liveAgents = agents.filter((a) => a.status === "published").length;

  const stats = [
    { label: "Agents", value: agents.length, hint: `${liveAgents} live`, icon: Bot, color: "text-violet-300" },
    { label: "Providers ready", value: `${availableProviders}/${providers.length}`, hint: "credentials configured", icon: Plug, color: "text-cyan-300" },
    { label: "Sessions (24h)", value: sessions.length, hint: "across all channels", icon: Activity, color: "text-emerald-300" },
    { label: "p95 latency", value: "287 ms", hint: "first-token to first-audio", icon: TrendingUp, color: "text-amber-300" },
  ];

  return (
    <div className="container py-8 space-y-8">
      {/* Hero card */}
      <div className="gradient-border rounded-2xl p-6 md:p-8">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Welcome back 👋</h1>
            <p className="text-muted-foreground mt-1">
              Build a voice agent in under 5 minutes — start from a template or design from scratch.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/dashboard/templates">
              <Button variant="gradient">
                <Sparkles className="h-4 w-4" />
                Start from template
              </Button>
            </Link>
            <Link href="/dashboard/playground">
              <Button variant="outline">
                <Mic className="h-4 w-4" />
                Open playground
              </Button>
            </Link>
          </div>
        </div>
      </div>

      {/* Stats */}
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
              <div className="mt-2 text-3xl font-bold tabular-nums">{s.value}</div>
              <div className="text-xs text-muted-foreground mt-1">{s.hint}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Latency chart + recent agents */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>End-to-end latency (last 24h)</CardTitle>
          </CardHeader>
          <CardContent className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={latencyDemo}>
                <defs>
                  <linearGradient id="latencyFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.7} />
                    <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="hour" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                <YAxis tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    background: "hsl(var(--card))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: 8,
                  }}
                />
                <Area type="monotone" dataKey="ms" stroke="#a78bfa" fill="url(#latencyFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent agents</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {aLoading ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            ) : agents.length === 0 ? (
              <div className="text-sm text-muted-foreground text-center py-8">
                No agents yet.{" "}
                <Link className="text-violet-300 hover:underline" href="/dashboard/templates">
                  Start from a template →
                </Link>
              </div>
            ) : (
              agents.slice(0, 6).map((a) => (
                <Link
                  key={a.id}
                  href={`/dashboard/agents/edit?id=${a.id}`}
                  className="flex items-center justify-between py-2 px-2 rounded-md hover:bg-muted transition-colors"
                >
                  <div className="min-w-0">
                    <div className="font-medium text-sm truncate">{a.name}</div>
                    <div className="text-xs text-muted-foreground truncate">
                      {a.llm_provider} · {a.tts_provider}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={a.status === "published" ? "success" : "default"}>
                      {a.status}
                    </Badge>
                    <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                </Link>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
