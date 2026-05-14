"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Bot,
  FileText,
  Loader2,
  Mic,
  Plug,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
} from "lucide-react";

import { api, type Agent, type DocumentRecord, type McpCatalogueEntry, type McpServerConfig, type Skill } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: agent } = useSWR<Agent>(id ? `agent-${id}` : null, () => api.getAgent(id));
  const { data: skills = [] } = useSWR<Skill[]>("skills", () => api.listSkills());

  const [form, setForm] = useState<Partial<Agent>>({});
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  useEffect(() => {
    if (agent) setForm(agent);
  }, [agent]);

  if (!agent) {
    return (
      <div className="container py-12 flex items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  function set<K extends keyof Agent>(k: K, v: Agent[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setBusy(true);
    try {
      await api.updateAgent(id, form);
      mutate(`agent-${id}`);
      mutate("agents");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    setPublishing(true);
    setFlash(null);
    try {
      const updated = await api.publishAgent(id);
      // Optimistic refresh: seed the SWR caches with the new record so the
      // badge swaps from "draft" → "published" instantly, then revalidate.
      await Promise.all([
        mutate(`agent-${id}`, updated, { revalidate: true }),
        mutate("agents", undefined, { revalidate: true }),
      ]);
      setFlash({ kind: "ok", msg: "Published. Agent is now live." });
    } catch (e: any) {
      setFlash({ kind: "err", msg: e?.message || "Publish failed" });
    } finally {
      setPublishing(false);
      setTimeout(() => setFlash(null), 3500);
    }
  }

  async function destroy() {
    const name = agent?.name ?? form.name ?? "this agent";
    if (!confirm(`Delete agent "${name}"? This cannot be undone.`)) return;
    await api.deleteAgent(id);
    router.push("/dashboard/agents");
  }

  function toggleSkill(sid: string) {
    const cur = (form.skills as string[] | undefined) || [];
    set("skills", cur.includes(sid) ? cur.filter((x) => x !== sid) : [...cur, sid]);
  }

  return (
    <div className="container py-8 space-y-6 max-w-5xl">
      <Link href="/dashboard/agents" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to agents
      </Link>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-violet-500/20 to-cyan-400/20 flex items-center justify-center">
            <Bot className="h-5 w-5 text-violet-300" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">{form.name}</h1>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={agent.status === "published" ? "success" : "default"}>
                {agent.status}
              </Badge>
              <span>•</span>
              <span>{agent.llm_provider}</span>
              <span>•</span>
              <span>{agent.tts_provider}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={destroy}>
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
          <Link href={`/dashboard/playground?agent=${id}`}>
            <Button variant="outline">
              <Mic className="h-4 w-4" /> Test
            </Button>
          </Link>
          {agent.status !== "published" && (
            <Button variant="secondary" onClick={publish} disabled={publishing}>
              {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {publishing ? "Publishing…" : "Publish"}
            </Button>
          )}
          <Button variant="gradient" onClick={save} disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </Button>
        </div>
      </div>

      {flash && (
        <div
          className={`rounded-md border px-4 py-2 text-sm ${
            flash.kind === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/10 text-rose-200"
          }`}
        >
          {flash.msg}
        </div>
      )}

      <Tabs defaultValue="behavior">
        <TabsList>
          <TabsTrigger value="behavior">Behaviour</TabsTrigger>
          <TabsTrigger value="voice">Voice & model</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="docs">Documents</TabsTrigger>
          <TabsTrigger value="mcp">MCP</TabsTrigger>
          <TabsTrigger value="channels">Channels</TabsTrigger>
        </TabsList>

        <TabsContent value="behavior">
          <Card>
            <CardContent className="pt-6 space-y-4">
              <div>
                <Label>Name</Label>
                <Input value={form.name || ""} onChange={(e) => set("name", e.target.value)} />
              </div>
              <div>
                <Label>System prompt</Label>
                <Textarea
                  rows={8}
                  value={form.system_prompt || ""}
                  onChange={(e) => set("system_prompt", e.target.value)}
                />
              </div>
              <div>
                <Label>Greeting</Label>
                <Input
                  value={form.greeting || ""}
                  onChange={(e) => set("greeting", e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Temperature</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={form.temperature ?? 0.7}
                    onChange={(e) => set("temperature", parseFloat(e.target.value))}
                  />
                </div>
                <div>
                  <Label>Max tokens</Label>
                  <Input
                    type="number"
                    value={form.max_tokens ?? 1024}
                    onChange={(e) => set("max_tokens", parseInt(e.target.value))}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="voice">
          <Card>
            <CardContent className="pt-6 space-y-4">
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <Label>LLM provider</Label>
                  <Select
                    value={form.llm_provider || "byteplus"}
                    onChange={(e) => set("llm_provider", e.target.value)}
                  >
                    <option value="byteplus">BytePlus</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="gemini">Gemini</option>
                    <option value="deepseek">DeepSeek</option>
                  </Select>
                </div>
                <div>
                  <Label>STT provider</Label>
                  <Select
                    value={form.stt_provider || "byteplus"}
                    onChange={(e) => set("stt_provider", e.target.value)}
                  >
                    <option value="byteplus">BytePlus</option>
                    <option value="deepgram">Deepgram</option>
                    <option value="assemblyai">AssemblyAI</option>
                    <option value="whisper">Whisper</option>
                  </Select>
                </div>
                <div>
                  <Label>TTS provider</Label>
                  <Select
                    value={form.tts_provider || "byteplus"}
                    onChange={(e) => set("tts_provider", e.target.value)}
                  >
                    <option value="byteplus">BytePlus</option>
                    <option value="elevenlabs">ElevenLabs</option>
                    <option value="cartesia">Cartesia</option>
                    <option value="openai">OpenAI TTS</option>
                  </Select>
                </div>
                <div>
                  <Label>Model</Label>
                  <Input value={form.llm_model || ""} onChange={(e) => set("llm_model", e.target.value)} />
                </div>
                <div>
                  <Label>Voice ID</Label>
                  <Input value={form.voice_id || ""} onChange={(e) => set("voice_id", e.target.value)} />
                </div>
                <div>
                  <Label>Speed</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={form.voice_speed ?? 1}
                    onChange={(e) => set("voice_speed", parseFloat(e.target.value))}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="skills">
          <Card>
            <CardHeader>
              <CardTitle>Skills attached to this agent</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {skills.length === 0 ? (
                <div className="text-sm text-muted-foreground">No skills installed.</div>
              ) : (
                skills.map((sk) => {
                  const enabled = (form.skills as string[] | undefined)?.includes(sk.id);
                  return (
                    <button
                      key={sk.id}
                      type="button"
                      onClick={() => toggleSkill(sk.id)}
                      className={`w-full text-left px-4 py-3 rounded-md border transition-colors ${
                        enabled
                          ? "border-primary/50 bg-primary/5"
                          : "border-border/60 hover:bg-muted"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <Sparkles
                            className={`h-4 w-4 ${enabled ? "text-violet-300" : "text-muted-foreground"}`}
                          />
                          <div>
                            <div className="font-medium text-sm">{sk.display_name}</div>
                            <div className="text-xs text-muted-foreground">{sk.description}</div>
                          </div>
                        </div>
                        {enabled && <Badge variant="primary">enabled</Badge>}
                      </div>
                    </button>
                  );
                })
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="docs">
          <DocumentsPanel agentId={id} />
        </TabsContent>

        <TabsContent value="mcp">
          <McpPanel
            value={(form.mcp_servers as McpServerConfig[] | undefined) || []}
            onChange={(v) => set("mcp_servers", v)}
          />
        </TabsContent>

        <TabsContent value="channels">
          <Card>
            <CardContent className="pt-6 space-y-3">
              <p className="text-sm text-muted-foreground">
                Add channels (web RTC, phone, WhatsApp, Telegram) — credentials are read from
                your <code className="text-foreground">.env</code> file.
              </p>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { id: "web", name: "Web (browser RTC)" },
                  { id: "phone", name: "Phone (Twilio)" },
                  { id: "whatsapp", name: "WhatsApp Business" },
                  { id: "telegram", name: "Telegram" },
                ].map((c) => (
                  <div key={c.id} className="px-4 py-3 rounded-md border border-border/60 flex items-center justify-between">
                    <div className="text-sm">{c.name}</div>
                    <Badge variant="default">configure</Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// DocumentsPanel — upload / list / delete docs for an agent's KB.
// Polls every 2s while any document is still indexing.
// ──────────────────────────────────────────────────────────────────────

function DocumentsPanel({ agentId }: { agentId: string }) {
  const swrKey = `agent-${agentId}-docs`;
  const { data: docs = [] } = useSWR<DocumentRecord[]>(
    swrKey,
    () => api.listDocuments(agentId),
    {
      // Poll every 2s if anything is still pending; otherwise stop.
      refreshInterval: (data) =>
        Array.isArray(data) && data.some((d) => !d.indexed && !d.error) ? 2000 : 0,
    },
  );
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [removingId, setRemovingId] = useState<string | null>(null);

  async function refresh() {
    await mutate(swrKey);
  }

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setUploading(true);
    setError("");
    try {
      await api.uploadDocument(agentId, f);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUploading(false);
      // Reset the input so the same file can be picked again later.
      e.target.value = "";
    }
  }

  async function remove(docId: string) {
    if (!confirm("Delete this document and its index?")) return;
    setRemovingId(docId);
    setError("");
    // Optimistic update — drop the row immediately, refetch on completion.
    try {
      await mutate(
        swrKey,
        (curr?: DocumentRecord[]) => (curr ?? []).filter((d) => d.id !== docId),
        { revalidate: false },
      );
      await api.deleteDocument(agentId, docId);
      await refresh();
    } catch (err) {
      setError(`Delete failed: ${(err as Error).message}`);
      await refresh(); // restore truth from server
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-violet-300" />
          Knowledge base
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Upload PDFs, Markdown / text files, or images. Text gets chunked and embedded
          (BytePlus Ark) so the agent can answer questions grounded in your content.
          Images are sent straight to the vision-capable LLM.
        </p>
        <label className="flex items-center gap-3 px-3 py-2 border border-dashed border-border/60 rounded-md cursor-pointer hover:border-primary/40">
          {uploading ? (
            <Loader2 className="h-4 w-4 animate-spin text-violet-300" />
          ) : (
            <Upload className="h-4 w-4 text-violet-300" />
          )}
          <span className="text-sm flex-1">
            {uploading ? "Uploading…" : "Click to upload a file"}
          </span>
          <input
            type="file"
            accept=".pdf,.txt,.md,.csv,.json,image/*"
            className="hidden"
            disabled={uploading}
            onChange={onPick}
          />
        </label>
        {error && <div className="text-xs text-rose-300">{error}</div>}

        {docs.length === 0 ? (
          <div className="text-sm text-muted-foreground text-center py-8">
            No documents yet.
          </div>
        ) : (
          <div className="space-y-2">
            {docs.map((d) => (
              <div
                key={d.id}
                className="flex items-center gap-3 px-3 py-2 rounded-md border border-border/60"
              >
                <FileText className="h-4 w-4 text-muted-foreground" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">{d.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {(d.size_bytes / 1024).toFixed(1)} KB · {d.page_count || "?"} page
                    {d.page_count === 1 ? "" : "s"} · {d.chunk_count} chunks
                  </div>
                </div>
                <Badge
                  variant={
                    !d.indexed && d.error
                      ? "danger"
                      : d.indexed && d.error
                        ? "warning"
                        : d.indexed
                          ? "success"
                          : "warning"
                  }
                  title={d.error || ""}
                >
                  {!d.indexed && d.error
                    ? "error"
                    : d.indexed && d.error
                      ? "keyword-only"
                      : d.indexed
                        ? "ready"
                        : "indexing…"}
                </Badge>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => remove(d.id)}
                  disabled={removingId === d.id}
                  aria-label="Delete document"
                >
                  {removingId === d.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────
// McpPanel — manage the agent's MCP server configs. Saved via the main
// "Save" button at the top of the page (we just mutate the form blob).
// ──────────────────────────────────────────────────────────────────────

function McpPanel({
  value,
  onChange,
}: {
  value: McpServerConfig[];
  onChange: (v: McpServerConfig[]) => void;
}) {
  const [draft, setDraft] = useState<McpServerConfig>({
    name: "",
    transport: "stdio",
    command: "",
    args: [],
    env: {},
    url: "",
  });
  const [argsText, setArgsText] = useState("");
  const [envText, setEnvText] = useState("");
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<string>("");
  // Catalogue modal state. We fetch the curated list lazily on first
  // open so cold loads of the agent edit page aren't slowed down.
  const [catalogueOpen, setCatalogueOpen] = useState(false);
  const [catalogue, setCatalogue] = useState<McpCatalogueEntry[] | null>(null);

  async function openCatalogue() {
    setCatalogueOpen(true);
    if (catalogue === null) {
      try {
        setCatalogue(await api.mcpCatalogue());
      } catch {
        setCatalogue([]);
      }
    }
  }

  function pickFromCatalogue(entry: McpCatalogueEntry) {
    // Pre-fill the form with the catalogue entry's shape. Required env
    // vars become empty `KEY=` lines so the user just fills the values.
    const envLines = entry.env_required.map((k) => `${k}=`).join("\n");
    setDraft({
      name: entry.id,
      transport: entry.transport,
      command: entry.command || "",
      args: entry.args || [],
      env: {},
      url: "",
    });
    setArgsText((entry.args || []).join(" "));
    setEnvText(envLines);
    setProbeResult(`Loaded "${entry.name}" — fill in the env values, then Probe + Add.`);
    setCatalogueOpen(false);
  }

  function reset() {
    setDraft({ name: "", transport: "stdio", command: "", args: [], env: {}, url: "" });
    setArgsText("");
    setEnvText("");
    setProbeResult("");
  }

  function parseDraft(): McpServerConfig {
    const args = argsText.trim()
      ? argsText.split(/\s+/).filter(Boolean)
      : [];
    const env: Record<string, string> = {};
    for (const line of envText.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      const eq = t.indexOf("=");
      if (eq <= 0) continue;
      env[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
    }
    return { ...draft, args, env };
  }

  async function probe() {
    setProbing(true);
    setProbeResult("");
    try {
      const r = await api.mcpProbe(parseDraft());
      setProbeResult(
        `✓ ${r.count} tool${r.count === 1 ? "" : "s"} — ${r.tools
          .slice(0, 5)
          .map((t) => t.display_name)
          .join(", ")}${r.tools.length > 5 ? "…" : ""}`,
      );
    } catch (e) {
      setProbeResult(`✗ ${(e as Error).message}`);
    } finally {
      setProbing(false);
    }
  }

  function add() {
    const cfg = parseDraft();
    if (!cfg.name.trim()) {
      setProbeResult("Name is required");
      return;
    }
    onChange([...(value || []), cfg]);
    reset();
  }

  function remove(idx: number) {
    const next = [...value];
    next.splice(idx, 1);
    onChange(next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Plug className="h-4 w-4 text-cyan-300" />
          MCP servers
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Connect to{" "}
          <a
            href="https://modelcontextprotocol.io/"
            target="_blank"
            rel="noreferrer"
            className="text-violet-300 hover:underline"
          >
            Model Context Protocol
          </a>{" "}
          servers to give this agent external tools (GitHub, filesystem,
          Slack, Postgres, etc.). Each server's tools are exposed to the LLM
          alongside built-in skills.
        </p>

        {value.length > 0 && (
          <div className="space-y-2">
            {value.map((cfg, i) => (
              <div
                key={i}
                className="flex items-center gap-3 px-3 py-2 rounded-md border border-border/60"
              >
                <Wand2 className="h-4 w-4 text-violet-300 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{cfg.name}</div>
                  <div className="text-xs text-muted-foreground font-mono truncate">
                    {cfg.transport === "stdio"
                      ? `${cfg.command} ${(cfg.args || []).join(" ")}`
                      : cfg.url}
                  </div>
                </div>
                <Badge variant="default">{cfg.transport}</Badge>
                <Button variant="ghost" size="icon" onClick={() => remove(i)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-3 border-t border-border/60 pt-4">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium text-foreground">Add a server</div>
            <Button variant="outline" size="sm" onClick={openCatalogue}>
              <Sparkles className="h-3.5 w-3.5" />
              Browse catalogue
            </Button>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Name</Label>
              <Input
                placeholder="github"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div>
              <Label>Transport</Label>
              <Select
                value={draft.transport}
                onChange={(e) =>
                  setDraft({ ...draft, transport: e.target.value as "stdio" | "sse" })
                }
              >
                <option value="stdio">stdio (local command)</option>
                <option value="sse">sse (remote URL)</option>
              </Select>
            </div>
          </div>

          {draft.transport === "stdio" ? (
            <>
              <div>
                <Label>Command</Label>
                <Input
                  placeholder="npx"
                  value={draft.command || ""}
                  onChange={(e) => setDraft({ ...draft, command: e.target.value })}
                />
              </div>
              <div>
                <Label>Args (space-separated)</Label>
                <Input
                  placeholder="-y @modelcontextprotocol/server-github"
                  value={argsText}
                  onChange={(e) => setArgsText(e.target.value)}
                />
              </div>
              <div>
                <Label>Env (KEY=VALUE per line)</Label>
                <Textarea
                  rows={3}
                  placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=ghp_..."
                  value={envText}
                  onChange={(e) => setEnvText(e.target.value)}
                  className="font-mono text-xs"
                />
              </div>
            </>
          ) : (
            <div>
              <Label>URL</Label>
              <Input
                placeholder="https://my-mcp.example.com/sse"
                value={draft.url || ""}
                onChange={(e) => setDraft({ ...draft, url: e.target.value })}
              />
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={probe} disabled={probing}>
              {probing && <Loader2 className="h-4 w-4 animate-spin" />}
              Probe
            </Button>
            <Button variant="gradient" onClick={add} disabled={probing}>
              <Plus className="h-4 w-4" />
              Add
            </Button>
            {probeResult && (
              <div
                className={`text-xs ${
                  probeResult.startsWith("✓") ? "text-emerald-300" : "text-rose-300"
                }`}
              >
                {probeResult}
              </div>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Tip: <code>npx -y @modelcontextprotocol/server-filesystem /path</code> for local
            files, or browse Anthropic's{" "}
            <a
              href="https://github.com/modelcontextprotocol/servers"
              target="_blank"
              rel="noreferrer"
              className="text-violet-300 hover:underline"
            >
              public registry
            </a>
            .
          </p>
        </div>
      </CardContent>

      {/* MCP catalogue modal — opens via "Browse catalogue" button above.
          Inline overlay rather than a dedicated component so the whole
          panel stays self-contained. */}
      {catalogueOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6"
          onClick={() => setCatalogueOpen(false)}
        >
          <div
            className="bg-background border border-border/60 rounded-lg max-w-3xl w-full max-h-[80vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-5 border-b border-border/60 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">MCP server catalogue</h3>
                <p className="text-sm text-muted-foreground">
                  One-click pre-fill. You'll still need to paste your own API keys / tokens.
                </p>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setCatalogueOpen(false)}>
                ✕
              </Button>
            </div>
            <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-3">
              {catalogue === null ? (
                <div className="col-span-2 flex items-center justify-center py-12">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : catalogue.length === 0 ? (
                <div className="col-span-2 text-center text-sm text-muted-foreground py-12">
                  Catalogue is empty. Edit{" "}
                  <code>packages/core/openvox/mcp/catalogue.json</code> to add entries.
                </div>
              ) : (
                catalogue.map((entry) => (
                  <button
                    key={entry.id}
                    onClick={() => pickFromCatalogue(entry)}
                    className="text-left p-4 rounded-md border border-border/60 hover:border-violet-500/60 hover:bg-muted/30 transition-colors"
                  >
                    <div className="flex items-start gap-3">
                      <span className="text-2xl shrink-0">{entry.icon || "🔌"}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-2">
                          <div className="font-medium">{entry.name}</div>
                          {entry.category && (
                            <Badge variant="default">{entry.category}</Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {entry.tagline}
                        </p>
                        {entry.env_required.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {entry.env_required.map((k) => (
                              <span
                                key={k}
                                className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30"
                              >
                                {k}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
