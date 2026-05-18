"use client";

import { mutate } from "swr";
import useSWR from "swr";
import { useState } from "react";
import {
  Clock,
  Copy,
  Loader2,
  Play,
  Plus,
  Trash2,
  CheckCircle2,
  XCircle,
  Pause,
  PlayCircle,
  History,
  AlertCircle,
  Link as LinkIcon,
} from "lucide-react";

import { api, type Agent, type JobRecord, type JobRunRecord } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { formatDate } from "@/lib/utils";

export default function SchedulesPage() {
  const { data: jobs = [], isLoading } = useSWR<JobRecord[]>("jobs", () => api.listJobs(), {
    refreshInterval: 5000,
  });
  const { data: agents = [] } = useSWR<Agent[]>("agents", () => api.listAgents());
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<JobRecord | null>(null);

  async function refresh() {
    await mutate("jobs");
  }

  return (
    <div className="container py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Schedules</h1>
          <p className="text-muted-foreground text-sm">
            Cron / interval triggers for agents, skills, and audio-batch jobs.
            Runs locally; no external scheduler required.
          </p>
        </div>
        <Button variant="gradient" onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" />
          New schedule
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : jobs.length === 0 ? (
        <EmptyState onCreate={() => setCreating(true)} />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {jobs.map((j) => (
            <JobCard
              key={j.id}
              job={j}
              agents={agents}
              onEdit={() => setEditing(j)}
              onRefresh={refresh}
            />
          ))}
        </div>
      )}

      {(creating || editing) && (
        <JobModal
          job={editing}
          agents={agents}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={async () => {
            setCreating(false);
            setEditing(null);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-2xl gradient-border p-12 text-center">
      <Clock className="h-10 w-10 mx-auto text-violet-300 mb-3" />
      <h3 className="text-lg font-semibold">No schedules yet</h3>
      <p className="text-sm text-muted-foreground mt-1 mb-6 max-w-md mx-auto">
        Build a recurring job — e.g. "every night at 8 PM, transcribe and analyse
        the recordings in <code>/data/recordings</code>".
      </p>
      <Button variant="gradient" onClick={onCreate}>
        <Plus className="h-4 w-4" />
        Create your first schedule
      </Button>
    </div>
  );
}

function JobCard({
  job,
  agents,
  onEdit,
  onRefresh,
}: {
  job: JobRecord;
  agents: Agent[];
  onEdit: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<"trigger" | "toggle" | "delete" | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const agentName = agents.find((a) => a.id === job.agent_id)?.name || "—";

  async function runNow() {
    setBusy("trigger");
    try {
      await api.triggerJob(job.id);
      await onRefresh();
    } finally {
      setBusy(null);
    }
  }

  async function toggle() {
    setBusy("toggle");
    try {
      await api.updateJob(job.id, { ...job, enabled: !job.enabled });
      await onRefresh();
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!confirm(`Delete schedule "${job.name}"?`)) return;
    setBusy("delete");
    try {
      await api.deleteJob(job.id);
      await onRefresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="hover:border-primary/40 transition-colors">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-violet-500/20 to-cyan-400/20 flex items-center justify-center">
              <Clock className="h-5 w-5 text-violet-300" />
            </div>
            <div>
              <CardTitle>{job.name}</CardTitle>
              <div className="text-xs text-muted-foreground mt-0.5">
                <code>{job.kind}</code>
                {job.agent_id && ` · ${agentName}`}
              </div>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            {job.enabled ? (
              <Badge variant="success">enabled</Badge>
            ) : (
              <Badge variant="default">paused</Badge>
            )}
            {job.last_status === "success" && (
              <Badge variant="success" className="flex items-center gap-1">
                <CheckCircle2 className="h-3 w-3" /> last ok
              </Badge>
            )}
            {job.last_status === "error" && (
              <Badge variant="danger" title={job.last_error} className="flex items-center gap-1">
                <XCircle className="h-3 w-3" /> last error
              </Badge>
            )}
            {job.last_status === "running" && (
              <Badge variant="warning">running</Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {job.description && (
          <p className="text-sm text-muted-foreground line-clamp-2">{job.description}</p>
        )}
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
          <div>
            <div className="uppercase tracking-wider">Trigger</div>
            <div className="font-mono text-foreground/90 mt-0.5">
              {job.trigger_type === "webhook"
                ? "webhook (fires on POST)"
                : `${job.trigger_type}: ${job.trigger_expr}`}
            </div>
          </div>
          <div>
            <div className="uppercase tracking-wider">
              {job.trigger_type === "webhook" ? "Last delivery" : "Next run"}
            </div>
            <div className="text-foreground/90 mt-0.5">
              {job.trigger_type === "webhook"
                ? job.last_run_at
                  ? formatDate(job.last_run_at)
                  : "never"
                : job.next_run_at
                  ? formatDate(job.next_run_at)
                  : "—"}
            </div>
          </div>
        </div>
        {job.trigger_type === "webhook" && job.webhook_url && (
          <WebhookUrlCallout url={job.webhook_url} />
        )}
        {job.last_error && (
          <div className="mt-3 text-xs text-rose-300 flex items-start gap-2">
            <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span className="break-all">{job.last_error}</span>
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" variant="gradient" onClick={runNow} disabled={busy !== null}>
            {busy === "trigger" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            Run now
          </Button>
          <Button size="sm" variant="outline" onClick={toggle} disabled={busy !== null}>
            {job.enabled ? (
              <>
                <Pause className="h-3.5 w-3.5" /> Pause
              </>
            ) : (
              <>
                <PlayCircle className="h-3.5 w-3.5" /> Enable
              </>
            )}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setHistoryOpen((v) => !v)}>
            <History className="h-3.5 w-3.5" />
            {historyOpen ? "Hide history" : "History"}
          </Button>
          <Button size="sm" variant="outline" onClick={onEdit}>
            Edit
          </Button>
          <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null}>
            {busy === "delete" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
        {historyOpen && <RunHistory jobId={job.id} />}
      </CardContent>
    </Card>
  );
}

function RunHistory({ jobId }: { jobId: string }) {
  const { data: runs = [], isLoading } = useSWR<JobRunRecord[]>(`job-${jobId}-runs`, () =>
    api.jobRuns(jobId),
  );
  if (isLoading) {
    return (
      <div className="mt-4 flex items-center justify-center py-6 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }
  if (runs.length === 0) {
    return <div className="mt-4 text-xs text-muted-foreground">No runs yet.</div>;
  }
  return (
    <div className="mt-4 border-t border-border/60 pt-3 space-y-2 max-h-64 overflow-y-auto">
      {runs.map((r) => (
        <div key={r.id} className="text-xs flex items-start gap-2">
          {r.status === "success" ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-success shrink-0 mt-0.5" />
          ) : r.status === "error" ? (
            <XCircle className="h-3.5 w-3.5 text-danger shrink-0 mt-0.5" />
          ) : (
            <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0 mt-0.5" />
          )}
          <div className="flex-1 min-w-0">
            <div className="text-foreground/90">{formatDate(r.started_at)}</div>
            {r.error ? (
              <div className="text-rose-300 break-all">{r.error}</div>
            ) : Object.keys(r.result || {}).length > 0 ? (
              <pre className="font-mono text-[10px] text-muted-foreground whitespace-pre-wrap overflow-hidden text-ellipsis max-h-20">
                {JSON.stringify(r.result, null, 2).slice(0, 400)}
              </pre>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function JobModal({
  job,
  agents,
  onClose,
  onSaved,
}: {
  job: JobRecord | null;
  agents: Agent[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const isEdit = !!job;
  const [form, setForm] = useState<Partial<JobRecord>>(
    job ?? {
      name: "Nightly audio analyser",
      description: "Transcribe + sentiment + profanity on each new file.",
      kind: "audio_batch",
      payload: { folder: "/data/recordings", glob: "*", language: "en-US" },
      agent_id: "",
      trigger_type: "cron",
      trigger_expr: "0 20 * * *",
      timezone: "UTC",
      enabled: true,
    },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [payloadText, setPayloadText] = useState(JSON.stringify(form.payload ?? {}, null, 2));

  function set<K extends keyof JobRecord>(k: K, v: JobRecord[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      let parsedPayload: Record<string, unknown> = {};
      try {
        parsedPayload = payloadText.trim() ? JSON.parse(payloadText) : {};
      } catch {
        throw new Error("Payload is not valid JSON");
      }
      const body = { ...form, payload: parsedPayload };
      if (isEdit && job) {
        await api.updateJob(job.id, body);
      } else {
        await api.createJob(body);
      }
      await onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function setKindPreset(kind: string) {
    set("kind", kind);
    if (kind === "audio_batch") {
      setPayloadText(
        JSON.stringify({ folder: "/data/recordings", glob: "*", language: "en-US" }, null, 2),
      );
    } else if (kind === "agent_query") {
      setPayloadText(JSON.stringify({ prompt: "Summarise the day's events." }, null, 2));
    } else if (kind === "skill_run") {
      setPayloadText(
        JSON.stringify({ skill_id: "get_quote", args: { ticker: "NVDA" } }, null, 2),
      );
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 animate-fade-in">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <CardTitle>{isEdit ? "Edit schedule" : "New schedule"}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Name</Label>
              <Input value={form.name || ""} onChange={(e) => set("name", e.target.value)} />
            </div>
            <div>
              <Label>Enabled</Label>
              <Select
                value={form.enabled ? "true" : "false"}
                onChange={(e) => set("enabled", e.target.value === "true")}
              >
                <option value="true">Yes — schedule active</option>
                <option value="false">No — paused</option>
              </Select>
            </div>
          </div>
          <div>
            <Label>Description</Label>
            <Input
              value={form.description || ""}
              onChange={(e) => set("description", e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Kind</Label>
              <Select value={form.kind} onChange={(e) => setKindPreset(e.target.value)}>
                <option value="agent_query">Agent query — text prompt → LLM answer</option>
                <option value="skill_run">Skill run — invoke a skill directly</option>
                <option value="audio_batch">Audio batch — transcribe + analyse a folder</option>
              </Select>
            </div>
            <div>
              <Label>Agent (optional)</Label>
              <Select value={form.agent_id || ""} onChange={(e) => set("agent_id", e.target.value)}>
                <option value="">— none —</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Trigger</Label>
              <Select
                value={form.trigger_type}
                onChange={(e) => set("trigger_type", e.target.value)}
              >
                <option value="cron">cron — &quot;0 20 * * *&quot; for 20:00 daily</option>
                <option value="interval">interval — e.g. &quot;5m&quot;, &quot;1h&quot;, &quot;1d&quot;</option>
                <option value="once">once — ISO datetime</option>
                <option value="webhook">webhook — fires on POST</option>
              </Select>
            </div>
            <div className="col-span-2">
              <Label>Expression</Label>
              <Input
                value={form.trigger_expr || ""}
                onChange={(e) => set("trigger_expr", e.target.value)}
                placeholder={
                  form.trigger_type === "cron"
                    ? "0 20 * * *"
                    : form.trigger_type === "interval"
                      ? "1h"
                      : form.trigger_type === "once"
                        ? "2026-05-12T20:00:00"
                        : "(ignored — webhook URL is generated on save)"
                }
                disabled={form.trigger_type === "webhook"}
              />
            </div>
          </div>

          <div>
            <Label>Payload (JSON)</Label>
            <Textarea
              rows={7}
              value={payloadText}
              onChange={(e) => setPayloadText(e.target.value)}
              className="font-mono text-xs"
            />
          </div>

          {error && <div className="text-xs text-rose-300">{error}</div>}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button variant="gradient" onClick={save} disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEdit ? "Save changes" : "Create schedule"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// WebhookUrlCallout — read-only URL display + copy button. Used by
// trigger_type="webhook" jobs so users can paste the fire URL into their
// integration without ever hand-constructing it. The URL embeds the
// per-job random token; treat it like an API key.
// ────────────────────────────────────────────────────────────────────────

function WebhookUrlCallout({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Older browsers / non-HTTPS contexts: fall back to the manual path.
      // Most users on localhost are fine; we just no-op rather than alert.
    }
  }

  return (
    <div className="mt-3 rounded-md border border-violet-500/40 bg-violet-500/5 p-3">
      <div className="flex items-center gap-2 mb-1.5 text-xs text-violet-300">
        <LinkIcon className="h-3 w-3" />
        <span className="uppercase tracking-wider">Webhook URL</span>
        <span className="text-muted-foreground normal-case tracking-normal">
          — POST here to fire this job
        </span>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 text-xs font-mono break-all bg-black/30 px-2 py-1.5 rounded">
          {url}
        </code>
        <Button size="sm" variant="outline" onClick={copy} className="shrink-0">
          <Copy className="h-3.5 w-3.5" />
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        Body optional. JSON object is merged into the job&apos;s payload for that one run.
        Treat the URL as an API key.
      </p>
    </div>
  );
}
