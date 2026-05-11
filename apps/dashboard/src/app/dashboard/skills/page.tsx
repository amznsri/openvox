"use client";

import { useState } from "react";
import useSWR from "swr";
import { Loader2, Play, Sparkles, Wand2 } from "lucide-react";

import { api, type Skill } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/input";

export default function SkillsPage() {
  const { data: skills = [], isLoading } = useSWR<Skill[]>("skills", () => api.listSkills());

  return (
    <div className="container py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Skills</h1>
        <p className="text-muted-foreground text-sm">
          Skills are tool/function-calls the LLM can invoke. Drop a Python file in{" "}
          <code className="text-foreground">~/.openvox/skills/</code> to add your own.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {skills.map((s) => (
            <SkillCard key={s.id} skill={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function SkillCard({ skill }: { skill: Skill }) {
  const [argsText, setArgsText] = useState(JSON.stringify(sampleArgs(skill), null, 2));
  const [result, setResult] = useState<string>("");
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setResult("");
    try {
      const args = argsText.trim() ? JSON.parse(argsText) : {};
      const r = await api.invokeSkill(skill.id, args);
      setResult(JSON.stringify(r, null, 2));
    } catch (e) {
      setResult(`Error: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-violet-500/20 to-cyan-400/20 flex items-center justify-center">
              <Wand2 className="h-4 w-4 text-violet-300" />
            </div>
            <div>
              <CardTitle className="text-sm">{skill.display_name}</CardTitle>
              <div className="font-mono text-[11px] text-muted-foreground">{skill.id}</div>
            </div>
          </div>
          <Badge variant="primary">
            <Sparkles className="h-3 w-3 mr-1" /> tool
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{skill.description}</p>
        <div className="mt-3 text-xs uppercase tracking-wider text-muted-foreground">
          Try it
        </div>
        <Textarea
          rows={5}
          value={argsText}
          onChange={(e) => setArgsText(e.target.value)}
          className="font-mono text-xs mt-1"
        />
        <div className="mt-2 flex justify-end">
          <Button size="sm" onClick={run} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Run
          </Button>
        </div>
        {result && (
          <pre className="mt-3 text-xs bg-muted/40 border border-border/40 rounded-md p-3 overflow-auto max-h-60">
            {result}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}

function sampleArgs(skill: Skill): Record<string, unknown> {
  const props = (skill.parameters as { properties?: Record<string, { type?: string }> }).properties;
  if (!props) return {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props)) {
    out[k] = v.type === "number" ? 0 : "";
  }
  return out;
}
