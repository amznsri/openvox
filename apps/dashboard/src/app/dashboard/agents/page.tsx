"use client";

import Link from "next/link";
import useSWR from "swr";
import { Bot, Plus, Sparkles, Mic, Pencil, Loader2 } from "lucide-react";

import { api, type Agent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "@/lib/utils";

export default function AgentsPage() {
  const { data: agents = [], isLoading } = useSWR<Agent[]>("agents", () => api.listAgents());

  return (
    <div className="container py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agents</h1>
          <p className="text-muted-foreground text-sm">
            Each agent bundles a prompt, voice, model, and skills.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/dashboard/templates">
            <Button variant="outline">
              <Sparkles className="h-4 w-4" />
              Browse templates
            </Button>
          </Link>
          <Link href="/dashboard/agents/new">
            <Button variant="gradient">
              <Plus className="h-4 w-4" />
              New agent
            </Button>
          </Link>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : agents.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {agents.map((a) => (
            <Card key={a.id} className="hover:border-primary/40 transition-colors">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-violet-500/20 to-cyan-400/20 flex items-center justify-center">
                      <Bot className="h-5 w-5 text-violet-300" />
                    </div>
                    <div>
                      <CardTitle>{a.name}</CardTitle>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {a.llm_provider} · {a.tts_provider}
                      </div>
                    </div>
                  </div>
                  <Badge variant={a.status === "published" ? "success" : "default"}>
                    {a.status}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground line-clamp-2 min-h-[2.5rem]">
                  {a.description || a.system_prompt}
                </p>
                <div className="mt-4 flex items-center gap-1.5 text-xs text-muted-foreground">
                  Updated {formatDate(a.updated_at)}
                </div>
                <div className="mt-4 flex gap-2">
                  <Link href={`/dashboard/agents/edit?id=${a.id}`} className="flex-1">
                    <Button variant="outline" size="sm" className="w-full">
                      <Pencil className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                  </Link>
                  <Link href={`/dashboard/playground?agent=${a.id}`}>
                    <Button variant="gradient" size="sm">
                      <Mic className="h-3.5 w-3.5" />
                      Test
                    </Button>
                  </Link>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl gradient-border p-12 text-center">
      <Bot className="h-10 w-10 mx-auto text-violet-300 mb-3" />
      <h3 className="text-lg font-semibold">No agents yet</h3>
      <p className="text-sm text-muted-foreground mt-1 mb-6">
        Spin up a production-quality agent in a single click — pick a template.
      </p>
      <div className="flex items-center justify-center gap-2">
        <Link href="/dashboard/templates">
          <Button variant="gradient">
            <Sparkles className="h-4 w-4" />
            Browse templates
          </Button>
        </Link>
        <Link href="/dashboard/agents/new">
          <Button variant="outline">
            Start blank
          </Button>
        </Link>
      </div>
    </div>
  );
}
