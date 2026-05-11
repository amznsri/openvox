"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Bot, Loader2, Save } from "lucide-react";
import Link from "next/link";

import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";

export default function NewAgentPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "My voice agent",
    description: "",
    llm_provider: "byteplus",
    // Empty → server will fill in BYTEPLUS_LLM_MODEL from .env on create.
    llm_model: "",
    stt_provider: "byteplus",
    tts_provider: "byteplus",
    // Empty → server will fill in BYTEPLUS_TTS_DEFAULT_VOICE from .env.
    voice_id: "",
    voice_language: "en-US",
    voice_speed: 1.0,
    system_prompt: "You are a helpful voice assistant. Keep responses under 2 sentences.",
    greeting: "Hi! How can I help you today?",
    temperature: 0.7,
    max_tokens: 1024,
  });

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setBusy(true);
    try {
      const a = await api.createAgent(form as any);
      router.push(`/dashboard/agents/${a.id}`);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container py-8 max-w-3xl">
      <Link href="/dashboard/agents" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4">
        <ArrowLeft className="h-4 w-4" />
        Back to agents
      </Link>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-violet-300" />
            New agent
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Name</Label>
              <Input value={form.name} onChange={(e) => set("name", e.target.value)} />
            </div>
            <div>
              <Label>Voice language</Label>
              <Select value={form.voice_language} onChange={(e) => set("voice_language", e.target.value)}>
                <option value="en-US">English (US)</option>
                <option value="en-GB">English (UK)</option>
                <option value="zh-CN">Chinese (Mandarin)</option>
                <option value="es-ES">Spanish</option>
                <option value="fr-FR">French</option>
                <option value="de-DE">German</option>
                <option value="ja-JP">Japanese</option>
              </Select>
            </div>
          </div>
          <div>
            <Label>Description</Label>
            <Input value={form.description} onChange={(e) => set("description", e.target.value)} />
          </div>
          <div>
            <Label>System prompt</Label>
            <Textarea
              rows={5}
              value={form.system_prompt}
              onChange={(e) => set("system_prompt", e.target.value)}
            />
          </div>
          <div>
            <Label>Greeting (first thing the agent says)</Label>
            <Input value={form.greeting} onChange={(e) => set("greeting", e.target.value)} />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>LLM provider</Label>
              <Select value={form.llm_provider} onChange={(e) => set("llm_provider", e.target.value)}>
                <option value="byteplus">BytePlus (Seed)</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="gemini">Gemini</option>
                <option value="deepseek">DeepSeek</option>
              </Select>
            </div>
            <div>
              <Label>STT provider</Label>
              <Select value={form.stt_provider} onChange={(e) => set("stt_provider", e.target.value)}>
                <option value="byteplus">BytePlus (Seed ASR)</option>
                <option value="deepgram">Deepgram</option>
                <option value="assemblyai">AssemblyAI</option>
                <option value="whisper">Whisper</option>
              </Select>
            </div>
            <div>
              <Label>TTS provider</Label>
              <Select value={form.tts_provider} onChange={(e) => set("tts_provider", e.target.value)}>
                <option value="byteplus">BytePlus (Seed TTS)</option>
                <option value="elevenlabs">ElevenLabs</option>
                <option value="cartesia">Cartesia</option>
                <option value="openai">OpenAI TTS</option>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Model</Label>
              <Input
                value={form.llm_model}
                onChange={(e) => set("llm_model", e.target.value)}
                placeholder="(use BYTEPLUS_LLM_MODEL from .env)"
              />
            </div>
            <div>
              <Label>Voice ID</Label>
              <Input
                value={form.voice_id}
                onChange={(e) => set("voice_id", e.target.value)}
                placeholder="(use BYTEPLUS_TTS_DEFAULT_VOICE from .env)"
              />
            </div>
            <div>
              <Label>Temperature</Label>
              <Input
                type="number"
                step="0.1"
                value={form.temperature}
                onChange={(e) => set("temperature", parseFloat(e.target.value))}
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Link href="/dashboard/agents">
              <Button variant="outline">Cancel</Button>
            </Link>
            <Button variant="gradient" onClick={save} disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Create agent
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
