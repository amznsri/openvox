import type { Agent, AgentBase, ProviderInfo, Template } from "./types.js";

export class OpenVoxClient {
  constructor(
    private readonly baseUrl: string = "http://localhost:3001",
    private readonly apiKey?: string,
  ) {}

  private async http<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...((init.headers as Record<string, string>) || {}),
    };
    if (this.apiKey) headers["Authorization"] = `Bearer ${this.apiKey}`;
    const r = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => "")}`);
    if (r.status === 204) return undefined as T;
    return (await r.json()) as T;
  }

  // ── Agents ─────────────────────────────────────────────────────
  agents = {
    list: () => this.http<Agent[]>("/api/v1/agents"),
    get: (id: string) => this.http<Agent>(`/api/v1/agents/${id}`),
    create: (body: AgentBase) =>
      this.http<Agent>("/api/v1/agents", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: Partial<AgentBase>) =>
      this.http<Agent>(`/api/v1/agents/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    publish: (id: string) =>
      this.http<Agent>(`/api/v1/agents/${id}/publish`, { method: "POST" }),
    delete: (id: string) =>
      this.http<void>(`/api/v1/agents/${id}`, { method: "DELETE" }),
  };

  // ── Templates ─────────────────────────────────────────────────
  templates = {
    list: () => this.http<Template[]>("/api/v1/templates"),
    instantiate: (id: string, name?: string) =>
      this.http<Agent>(`/api/v1/templates/${id}/instantiate`, {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
  };

  // ── Providers ─────────────────────────────────────────────────
  providers = {
    list: (type?: string) =>
      this.http<ProviderInfo[]>(`/api/v1/providers${type ? `?type=${type}` : ""}`),
  };

  // ── Skills ────────────────────────────────────────────────────
  skills = {
    list: () => this.http<unknown[]>("/api/v1/skills"),
    invoke: (skill_id: string, args: Record<string, unknown>) =>
      this.http<{ ok: boolean; output: unknown; error: string }>("/api/v1/skills/invoke", {
        method: "POST",
        body: JSON.stringify({ skill_id, args }),
      }),
  };

  // ── Helpers ───────────────────────────────────────────────────
  wsUrl(): string {
    return this.baseUrl.replace(/^http/, "ws");
  }
}
