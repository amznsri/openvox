/**
 * Thin REST client for the OpenVox API gateway.
 * The dashboard talks to the Node gateway at NEXT_PUBLIC_API_URL,
 * which proxies to the Python core.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001";
const WS = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:3001";

async function http<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}

export const api = {
  // Agents
  listAgents: () => http<Agent[]>("/api/v1/agents"),
  createAgent: (body: Partial<Agent>) =>
    http<Agent>("/api/v1/agents", { method: "POST", body: JSON.stringify(body) }),
  getAgent: (id: string) => http<Agent>(`/api/v1/agents/${id}`),
  updateAgent: (id: string, body: Partial<Agent>) =>
    http<Agent>(`/api/v1/agents/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  publishAgent: (id: string) =>
    http<Agent>(`/api/v1/agents/${id}/publish`, { method: "POST" }),
  deleteAgent: (id: string) =>
    http<void>(`/api/v1/agents/${id}`, { method: "DELETE" }),

  // Templates
  listTemplates: () => http<Template[]>("/api/v1/templates"),
  getTemplate: (id: string) => http<Template>(`/api/v1/templates/${id}`),
  instantiateTemplate: (id: string, name?: string) =>
    http<Agent>(`/api/v1/templates/${id}/instantiate`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  // Providers / skills
  listProviders: (type?: string) =>
    http<Provider[]>(`/api/v1/providers${type ? `?type=${type}` : ""}`),
  listVoices: () => http<Record<string, Voice[]>>("/api/v1/providers/voices"),
  listSkills: () => http<Skill[]>("/api/v1/skills"),
  invokeSkill: (skill_id: string, args: Record<string, unknown>) =>
    http<{ ok: boolean; output: unknown; error: string }>("/api/v1/skills/invoke", {
      method: "POST",
      body: JSON.stringify({ skill_id, args }),
    }),

  // Sessions
  listSessions: (agentId?: string) =>
    http<Session[]>(`/api/v1/sessions${agentId ? `?agent_id=${agentId}` : ""}`),
  getSession: (id: string) => http<Session>(`/api/v1/sessions/${id}`),
  getTranscripts: (id: string) => http<TranscriptLine[]>(`/api/v1/sessions/${id}/transcripts`),

  // Playground (text mode)
  textChat: async (body: TextChatRequest, onToken: (t: string) => void) => {
    const r = await fetch(`${BASE}/api/v1/playground/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok || !r.body) throw new Error(`${r.status} ${r.statusText}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      onToken(dec.decode(value));
    }
  },

  // RTC token
  rtcToken: (room_id: string, user_id: string, role = "publisher") =>
    http<RtcToken>("/api/v1/rtc/token", {
      method: "POST",
      body: JSON.stringify({ room_id, user_id, role }),
    }),

  // Audio file analysis (Audio Analyzer template)
  analyzeAudio: async (file: File, opts: AudioAnalyzeOpts = {}): Promise<AudioAnalysis> => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("language", opts.language ?? "en-US");
    fd.append("sentiment", String(opts.sentiment ?? true));
    fd.append("profanity", String(opts.profanity ?? true));
    const r = await fetch(`${BASE}/api/v1/playground/audio_analyze`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => "")}`);
    return r.json();
  },

  // One-shot STT: audio blob → transcript text.
  transcribe: async (blob: Blob, language = "en-US"): Promise<{ transcript: string }> => {
    const fd = new FormData();
    fd.append("file", blob, "recording.webm");
    fd.append("language", language);
    const r = await fetch(`${BASE}/api/v1/playground/transcribe`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => "")}`);
    return r.json();
  },

  // One-shot TTS: text → PCM bytes (s16le mono). Returns the raw audio
  // plus the sample rate from the response header so the playback queue
  // can decode without guessing.
  synthesize: async (
    text: string,
    opts: { voice_id?: string; sample_rate?: number; speed?: number } = {},
  ): Promise<{ audio: ArrayBuffer; sampleRate: number }> => {
    const r = await fetch(`${BASE}/api/v1/playground/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice_id: opts.voice_id,
        sample_rate: opts.sample_rate ?? 24000,
        speed: opts.speed ?? 1.0,
      }),
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => "")}`);
    const sampleRate = parseInt(r.headers.get("X-Sample-Rate") || "24000", 10);
    return { audio: await r.arrayBuffer(), sampleRate };
  },

  // Documents (Document Q&A template)
  listDocuments: (agentId: string) =>
    http<DocumentRecord[]>(`/api/v1/agents/${agentId}/documents`),
  uploadDocument: async (agentId: string, file: File): Promise<DocumentRecord> => {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch(`${BASE}/api/v1/agents/${agentId}/documents`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => "")}`);
    return r.json();
  },
  deleteDocument: (agentId: string, docId: string) =>
    http<void>(`/api/v1/agents/${agentId}/documents/${docId}`, { method: "DELETE" }),
  queryDocuments: (agent_id: string, question: string, top_k = 5) =>
    http<DocumentQueryResult>("/api/v1/playground/document_query", {
      method: "POST",
      body: JSON.stringify({ agent_id, question, top_k }),
    }),

  // Scheduled jobs
  listJobs: () => http<JobRecord[]>("/api/v1/jobs"),
  createJob: (body: Partial<JobRecord>) =>
    http<JobRecord>("/api/v1/jobs", { method: "POST", body: JSON.stringify(body) }),
  updateJob: (id: string, body: Partial<JobRecord>) =>
    http<JobRecord>(`/api/v1/jobs/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteJob: (id: string) =>
    http<void>(`/api/v1/jobs/${id}`, { method: "DELETE" }),
  triggerJob: (id: string) =>
    http<{ queued: boolean }>(`/api/v1/jobs/${id}/trigger`, { method: "POST" }),
  jobRuns: (id: string) =>
    http<JobRunRecord[]>(`/api/v1/jobs/${id}/runs`),

  // MCP — probe a server config without saving it.
  mcpProbe: (cfg: McpServerConfig) =>
    http<{ tools: { id: string; display_name: string; description: string }[]; count: number }>(
      "/api/v1/mcp/probe",
      { method: "POST", body: JSON.stringify(cfg) },
    ),
};

export const wsUrl = (path: string) => `${WS}${path}`;

// ── Types ────────────────────────────────────────────────────────────
export type Agent = {
  id: string;
  name: string;
  description: string;
  template_id: string | null;
  stt_provider: string;
  tts_provider: string;
  llm_provider: string;
  llm_model: string;
  voice_id: string;
  voice_speed: number;
  voice_language: string;
  system_prompt: string;
  greeting: string;
  temperature: number;
  max_tokens: number;
  skills: string[];
  channels: Record<string, unknown>;
  mcp_servers: McpServerConfig[];
  voice_map: Record<string, string>;
  status: string;
  created_at: string;
  updated_at: string;
};

export type McpServerConfig = {
  name: string;
  transport: "stdio" | "sse";
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
};

export type Template = {
  id: string;
  name: string;
  tagline: string;
  category: string;
  icon: string;
  use_cases: string[];
  default: Partial<Agent>;
};

export type Provider = {
  id: string;
  type: string;
  display_name: string;
  capabilities: string[];
  available: boolean;
};

export type Voice = { id: string; name: string };

export type Skill = {
  id: string;
  display_name: string;
  description: string;
  parameters: Record<string, unknown>;
  config_schema: Record<string, unknown>;
};

export type Session = {
  id: string;
  agent_id: string;
  channel: string;
  caller_id: string;
  duration_ms: number;
  turn_count: number;
  cost_usd: number;
  first_token_ms: number;
  avg_response_ms: number;
  status: string;
  audio_url: string;
  transcript_url: string;
  started_at: string;
  ended_at: string | null;
};

export type TranscriptLine = {
  id: string;
  role: string;
  text: string;
  audio_url: string;
  started_ms: number;
  ended_ms: number;
  skill_id: string;
  skill_args: Record<string, unknown>;
  skill_result: Record<string, unknown>;
  sentiment: string;
  created_at: string;
};

export type TextChatRequest = {
  provider?: string;
  model?: string;
  system?: string;
  user: string;
  temperature?: number;
  max_tokens?: number;
};

export type RtcToken = {
  provider: string;
  app_id: string;
  room_id: string;
  user_id: string;
  token: string;
  expire_at: number;
  sdk_npm?: string;
};

export type AudioAnalyzeOpts = {
  language?: string;
  sentiment?: boolean;
  profanity?: boolean;
};

export type AudioAnalysis = {
  transcript: string;
  utterances: { text: string; start_ms: number; end_ms: number }[];
  duration_ms: number;
  filename?: string;
  sentiment?: { ok: boolean; output?: { label: string; confidence: number; method: string } };
  profanity?: { ok: boolean; output?: { hits: string[]; severity: number; language: string } };
};

export type DocumentRecord = {
  id: string;
  agent_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  page_count: number;
  chunk_count: number;
  indexed: boolean;
  error: string;
  created_at: string;
};

export type DocumentQueryResult = {
  answer: string;
  passages: { source: string; page: number; kind: string; score: number; snippet: string }[];
  note?: string;
};

export type JobRecord = {
  id: string;
  name: string;
  description: string;
  kind: "agent_query" | "skill_run" | "audio_batch" | string;
  payload: Record<string, unknown>;
  agent_id: string;
  trigger_type: "cron" | "interval" | "once" | string;
  trigger_expr: string;
  timezone: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string;
  last_error: string;
  created_at: string;
  updated_at: string;
};

export type JobRunRecord = {
  id: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  result: Record<string, unknown>;
  error: string;
};
