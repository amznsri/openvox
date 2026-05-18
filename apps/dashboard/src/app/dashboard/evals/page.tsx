"use client";

/**
 * Evals dashboard — list eval runs, see verdict / judge breakdown,
 * spin up new runs against a recording OR a synthetic persona.
 *
 * Backend lives at /api/v1/evals/* (shipped Session 8). This page is
 * pure UI — list, drawer, modal — over the existing routes.
 */

import { useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  ClipboardCheck,
  Loader2,
  Plus,
  Play,
  CheckCircle2,
  XCircle,
  CircleDashed,
  AlertCircle,
  X,
  Trash2,
} from "lucide-react";

import {
  api,
  type Agent,
  type EvalRunRecord,
  type Persona,
  type RecordingRecord,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { formatDate, formatDuration } from "@/lib/utils";

export default function EvalsPage() {
  const { data: runs = [], isLoading } = useSWR<EvalRunRecord[]>(
    "eval-runs",
    () => api.listEvalRuns(50),
    { refreshInterval: 4000 }, // poll so in-progress runs flip to completed
  );
  const { data: agents = [] } = useSWR<Agent[]>("agents", () => api.listAgents());
  const { data: personas = [] } = useSWR<Persona[]>("eval-personas", () => api.listPersonas());
  const { data: recordings = [] } = useSWR<RecordingRecord[]>(
    "eval-recordings",
    () => api.listRecordings(),
  );

  const [runOpen, setRunOpen] = useState(false);
  const [drawerRunId, setDrawerRunId] = useState<string | null>(null);

  const passCount = runs.filter((r) => r.verdict === "pass").length;
  const failCount = runs.filter((r) => r.verdict === "fail").length;
  const partialCount = runs.filter((r) => r.verdict === "partial").length;
  const runningCount = runs.filter((r) => r.verdict === "running").length;

  return (
    <div className="container py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Evals</h1>
          <p className="text-muted-foreground text-sm">
            Replay recordings or spar against synthetic personas to catch agent regressions.
          </p>
        </div>
        <Button variant="gradient" onClick={() => setRunOpen(true)} disabled={agents.length === 0}>
          <Plus className="h-4 w-4" />
          Run eval
        </Button>
      </div>

      {/* Top stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total runs" value={runs.length} icon={ClipboardCheck} color="text-violet-300" />
        <StatCard label="Pass" value={passCount} icon={CheckCircle2} color="text-emerald-300" />
        <StatCard label="Fail" value={failCount} icon={XCircle} color="text-rose-300" />
        <StatCard label="Partial / running" value={partialCount + runningCount} icon={CircleDashed} color="text-amber-300" />
      </div>

      {/* Runs list */}
      <Card>
        <CardHeader>
          <CardTitle>Recent runs</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : runs.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              No runs yet. Click <span className="text-foreground font-medium">Run eval</span> to
              spar an agent against a persona, or save a session as a recording from the
              Observability page and replay it.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs uppercase tracking-wider text-muted-foreground border-b border-border/40">
                    <th className="text-left py-2 px-3">Verdict</th>
                    <th className="text-left py-2 px-3">Agent</th>
                    <th className="text-left py-2 px-3">Against</th>
                    <th className="text-left py-2 px-3">Turns</th>
                    <th className="text-left py-2 px-3">Duration</th>
                    <th className="text-left py-2 px-3">Started</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr
                      key={r.id}
                      onClick={() => setDrawerRunId(r.id)}
                      className="border-b border-border/40 hover:bg-muted/30 transition-colors cursor-pointer"
                    >
                      <td className="py-2 px-3">
                        <VerdictBadge verdict={r.verdict} score={r.score} />
                      </td>
                      <td className="py-2 px-3 font-mono text-xs">{r.agent_id.slice(0, 8)}…</td>
                      <td className="py-2 px-3 text-xs">
                        {r.persona_id
                          ? <span className="text-violet-300">persona: {(personas.find((p) => p.id === r.persona_id)?.name) || r.persona_id.slice(0, 8)}</span>
                          : r.recording_id
                            ? <span className="text-cyan-300">replay: {(recordings.find((rec) => rec.id === r.recording_id)?.name) || r.recording_id.slice(0, 8)}</span>
                            : "—"}
                      </td>
                      <td className="py-2 px-3 tabular-nums">{r.turn_count || "—"}</td>
                      <td className="py-2 px-3 tabular-nums">
                        {r.duration_ms ? formatDuration(r.duration_ms) : "—"}
                      </td>
                      <td className="py-2 px-3 text-muted-foreground">{formatDate(r.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Personas + Recordings panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PersonasPanel personas={personas} />
        <RecordingsPanel recordings={recordings} />
      </div>

      {runOpen && (
        <RunEvalModal
          agents={agents}
          personas={personas}
          recordings={recordings}
          onClose={() => setRunOpen(false)}
          onCreated={(run) => {
            globalMutate("eval-runs");
            setRunOpen(false);
            setDrawerRunId(run.id);
          }}
        />
      )}

      {drawerRunId && (
        <RunDetailDrawer
          runId={drawerRunId}
          onClose={() => setDrawerRunId(null)}
        />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Stat cards
// ──────────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: number | string;
  icon: any;
  color: string;
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground uppercase tracking-wider">{label}</span>
          <Icon className={`h-4 w-4 ${color}`} />
        </div>
        <div className="mt-2 text-2xl font-bold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Verdict badge with score
// ──────────────────────────────────────────────────────────────────────

function VerdictBadge({ verdict, score }: { verdict: string; score: number }) {
  const variant =
    verdict === "pass" ? "success"
    : verdict === "fail" ? "danger"
    : verdict === "partial" ? "warning"
    : "default";
  const icon =
    verdict === "pass" ? <CheckCircle2 className="h-3 w-3" />
    : verdict === "fail" ? <XCircle className="h-3 w-3" />
    : verdict === "running" ? <Loader2 className="h-3 w-3 animate-spin" />
    : verdict === "error" ? <AlertCircle className="h-3 w-3" />
    : <CircleDashed className="h-3 w-3" />;
  return (
    <Badge variant={variant as any} className="inline-flex items-center gap-1">
      {icon}
      <span>{verdict}</span>
      {(verdict === "pass" || verdict === "fail" || verdict === "partial") && (
        <span className="opacity-70 font-mono ml-0.5">{(score * 100).toFixed(0)}%</span>
      )}
    </Badge>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Personas / Recordings panels (read-only lists for now; CRUD is
// handled via the API + the seed personas appear automatically).
// ──────────────────────────────────────────────────────────────────────

function PersonasPanel({ personas }: { personas: Persona[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Personas ({personas.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {personas.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No personas yet. The core ships five built-ins on startup —
            if you see this, the seed didn&apos;t run.
          </p>
        ) : (
          <div className="space-y-2">
            {personas.map((p) => (
              <div
                key={p.id}
                className="flex items-start justify-between gap-2 p-2.5 rounded-md border border-border/60"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium flex items-center gap-1.5">
                    {p.name}
                    {p.builtin && <Badge variant="default">builtin</Badge>}
                  </div>
                  <div className="text-xs text-muted-foreground line-clamp-2">{p.description}</div>
                </div>
                <div className="flex flex-wrap gap-1 shrink-0 max-w-[40%] justify-end">
                  {(p.tags || []).slice(0, 3).map((t) => (
                    <span
                      key={t}
                      className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-muted/60 text-muted-foreground"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RecordingsPanel({ recordings }: { recordings: RecordingRecord[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recordings ({recordings.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {recordings.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No recordings yet. From the{" "}
            <a href="/dashboard/observability" className="text-violet-300 hover:underline">
              Observability
            </a>{" "}
            page, click any session row and use &ldquo;Save as recording&rdquo;.
          </p>
        ) : (
          <div className="space-y-2">
            {recordings.map((r) => (
              <div
                key={r.id}
                className="flex items-start justify-between gap-2 p-2.5 rounded-md border border-border/60"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{r.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {r.turn_count} user turn{r.turn_count === 1 ? "" : "s"} ·{" "}
                    saved {formatDate(r.created_at)}
                  </div>
                </div>
                <div className="text-xs text-muted-foreground shrink-0">
                  {r.transcript.length} message{r.transcript.length === 1 ? "" : "s"}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Run-eval modal — pick agent + (persona OR recording) + criteria
// ──────────────────────────────────────────────────────────────────────

function RunEvalModal({
  agents,
  personas,
  recordings,
  onClose,
  onCreated,
}: {
  agents: Agent[];
  personas: Persona[];
  recordings: RecordingRecord[];
  onClose: () => void;
  onCreated: (run: EvalRunRecord) => void;
}) {
  const [agentId, setAgentId] = useState(agents[0]?.id || "");
  const [mode, setMode] = useState<"persona" | "recording">("persona");
  const [personaId, setPersonaId] = useState(personas[0]?.id || "");
  const [recordingId, setRecordingId] = useState(recordings[0]?.id || "");
  const [criteriaText, setCriteriaText] = useState(
    "Did the agent stay polite throughout?\nDid the agent provide a resolution or clear next step?",
  );
  const [maxTurns, setMaxTurns] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function start() {
    setBusy(true);
    setError("");
    try {
      const criteria = criteriaText
        .split("\n")
        .map((c) => c.trim())
        .filter(Boolean);
      const body: any = {
        agent_id: agentId,
        criteria,
        max_turns: maxTurns,
      };
      if (mode === "persona") body.persona_id = personaId;
      else body.recording_id = recordingId;
      const run = await api.runEval(body);
      onCreated(run);
    } catch (e: any) {
      setError(e?.message || "run failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-background border border-border/60 rounded-lg max-w-xl w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-border/60 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Run eval</h3>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <Label>Agent</Label>
            <Select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} {a.status === "published" ? "" : `(${a.status})`}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <Label>Test against</Label>
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setMode("persona")}
                className={`p-3 rounded-md border text-left ${
                  mode === "persona"
                    ? "border-violet-500 bg-violet-500/10"
                    : "border-border/60 hover:bg-muted/40"
                }`}
              >
                <div className="text-sm font-medium">🎭 Synthetic persona</div>
                <div className="text-xs text-muted-foreground">
                  Two-agent dialogue. Catches behavioural regressions.
                </div>
              </button>
              <button
                onClick={() => setMode("recording")}
                disabled={recordings.length === 0}
                className={`p-3 rounded-md border text-left disabled:opacity-50 disabled:cursor-not-allowed ${
                  mode === "recording"
                    ? "border-cyan-500 bg-cyan-500/10"
                    : "border-border/60 hover:bg-muted/40"
                }`}
              >
                <div className="text-sm font-medium">⏪ Replay recording</div>
                <div className="text-xs text-muted-foreground">
                  Re-run a saved session. Catches output drift.
                </div>
              </button>
            </div>
          </div>

          {mode === "persona" ? (
            <div>
              <Label>Persona</Label>
              <Select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
              {personas.find((p) => p.id === personaId)?.description && (
                <p className="text-xs text-muted-foreground mt-1">
                  {personas.find((p) => p.id === personaId)?.description}
                </p>
              )}
            </div>
          ) : (
            <div>
              <Label>Recording</Label>
              <Select value={recordingId} onChange={(e) => setRecordingId(e.target.value)}>
                {recordings.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} — {r.turn_count} turns
                  </option>
                ))}
              </Select>
            </div>
          )}

          <div>
            <Label>Pass criteria (one per line)</Label>
            <Textarea
              rows={4}
              value={criteriaText}
              onChange={(e) => setCriteriaText(e.target.value)}
              className="text-sm"
              placeholder="e.g.&#10;Did the agent collect the order number?&#10;Did the agent stay polite throughout?"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              The LLM judge evaluates each line independently. Leave blank for a
              soft &ldquo;was this conversation reasonable&rdquo; check.
            </p>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Max turns</Label>
              <Input
                type="number"
                min={2}
                max={20}
                value={maxTurns}
                onChange={(e) => setMaxTurns(parseInt(e.target.value) || 6)}
              />
            </div>
          </div>

          {error && (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/10 text-rose-200 text-sm px-3 py-2">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button variant="gradient" onClick={start} disabled={busy || !agentId}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              Start eval
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Run-detail drawer — verdict + transcript + per-criterion judge breakdown
// ──────────────────────────────────────────────────────────────────────

function RunDetailDrawer({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run, isLoading } = useSWR<EvalRunRecord>(
    runId ? `eval-run-${runId}` : null,
    () => api.getEvalRun(runId),
    { refreshInterval: (data) => (data?.verdict === "running" ? 2000 : 0) },
  );

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
            <h3 className="text-lg font-semibold">Eval run</h3>
            <p className="text-xs text-muted-foreground font-mono mt-0.5">{runId}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {isLoading || !run ? (
          <div className="flex items-center justify-center py-24 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : (
          <div className="p-5 space-y-5">
            <div className="flex items-center gap-3">
              <VerdictBadge verdict={run.verdict} score={run.score} />
              {run.duration_ms > 0 && (
                <span className="text-xs text-muted-foreground">
                  {formatDuration(run.duration_ms)} · {run.turn_count} turn{run.turn_count === 1 ? "" : "s"}
                </span>
              )}
            </div>

            {run.error && (
              <div className="rounded-md border border-rose-500/40 bg-rose-500/10 text-rose-200 text-sm px-3 py-2 break-all">
                {run.error}
              </div>
            )}

            {/* Per-criterion judge breakdown */}
            {run.judge_breakdown.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Judge breakdown</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {run.judge_breakdown.map((c, i) => (
                    <div
                      key={i}
                      className="border border-border/60 rounded-md p-2.5"
                    >
                      <div className="flex items-start justify-between gap-2 mb-1">
                        <div className="text-sm font-medium">{c.criterion}</div>
                        <VerdictBadge verdict={c.verdict} score={0} />
                      </div>
                      <div className="text-xs text-muted-foreground">{c.reasoning}</div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}

            {/* Transcript */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Transcript</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {run.transcript.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No transcript captured.</p>
                ) : (
                  run.transcript.map((m, i) => (
                    <div key={i} className="flex gap-3">
                      <span
                        className={`text-[10px] uppercase tracking-wider font-bold shrink-0 w-16 pt-0.5 ${
                          m.role === "user"
                            ? "text-cyan-300"
                            : m.role === "assistant"
                              ? "text-violet-300"
                              : "text-amber-300"
                        }`}
                      >
                        {m.role}
                      </span>
                      <span className="text-sm flex-1 break-words">{m.text}</span>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
