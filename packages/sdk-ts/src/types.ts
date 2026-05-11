export type AgentBase = {
  name: string;
  description?: string;
  stt_provider?: string;
  tts_provider?: string;
  llm_provider?: string;
  llm_model?: string;
  voice_id?: string;
  voice_speed?: number;
  voice_language?: string;
  system_prompt?: string;
  greeting?: string;
  temperature?: number;
  max_tokens?: number;
  skills?: string[];
};

export type Agent = AgentBase & {
  id: string;
  status: "draft" | "published" | "archived" | string;
  created_at: string;
  updated_at: string;
};

export type Template = {
  id: string;
  name: string;
  tagline: string;
  category: string;
  icon: string;
  use_cases: string[];
  default: AgentBase;
};

export type ProviderInfo = {
  id: string;
  type: "llm" | "stt" | "tts" | "rtc" | string;
  display_name: string;
  capabilities: string[];
  available: boolean;
};

export type VoiceEvent =
  | { type: "user_partial"; text: string }
  | { type: "user_final"; text: string }
  | { type: "assistant_token"; text: string }
  | { type: "assistant_done"; text: string }
  | { type: "skill_call"; name: string; args: unknown }
  | { type: "skill_result"; name: string; output: unknown }
  | { type: "interrupt" }
  | { type: "error"; message: string }
  | { type: "audio"; chunk: ArrayBuffer; sampleRate: number; encoding: string };

export type VoiceSessionOptions = {
  agentId?: string;
  systemPrompt?: string;
  llmProvider?: string;
  llmModel?: string;
  sttProvider?: string;
  ttsProvider?: string;
  voiceId?: string;
  voiceLanguage?: string;
  sampleRate?: number;
};
