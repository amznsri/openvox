"use client";

/**
 * /dashboard/agents/new — Session 10 chooser between Form and Voice setup.
 *
 *   ?mode=form   → renders the existing form flow (backward compatible).
 *   ?mode=voice  → renders the Setup Assistant split-pane.
 *   (no mode)    → renders the chooser cards.
 *
 * The form code below is unchanged from the pre-Session-10 page; it
 * just got extracted into <FormFlow /> so we can mount it conditionally.
 *
 * useSearchParams() requires a Suspense boundary or the build-time
 * prerender step blows up. We wrap the inner switcher accordingly.
 */

import Link from "next/link";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import {
  ArrowLeft,
  Bot,
  FileEdit,
  Loader2,
  Mic,
  Save,
  Sparkles,
} from "lucide-react";

import { SetupAssistant } from "@/components/setup/SetupAssistant";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";

export default function NewAgentPage() {
  return (
    <Suspense fallback={<ChooserScreen />}>
      <NewAgentSwitch />
    </Suspense>
  );
}

function NewAgentSwitch() {
  const params = useSearchParams();
  const mode = (params.get("mode") || "").toLowerCase();

  if (mode === "voice") return <SetupAssistant />;
  if (mode === "form") return <FormFlow />;
  return <ChooserScreen />;
}

// ──────────────────────────────────────────────────────────────────────
// Chooser — first thing a non-technical user sees when they click
// "New agent". Two big cards, no jargon.
// ──────────────────────────────────────────────────────────────────────

function ChooserScreen() {
  return (
    <div className="container py-8 max-w-4xl">
      <Link
        href="/dashboard/agents"
        className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to agents
      </Link>

      <div className="mb-6">
        <h1 className="text-2xl font-bold">Create a new agent</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Two ways to start — pick whichever fits how you work.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link href="/dashboard/agents/new?mode=voice" className="block group">
          <Card className="h-full hover:border-violet-500/60 transition-colors cursor-pointer">
            <CardContent className="pt-6">
              <div className="flex items-start gap-3">
                <div className="h-12 w-12 rounded-lg bg-gradient-to-br from-violet-500/20 to-cyan-400/20 flex items-center justify-center shrink-0">
                  <Mic className="h-6 w-6 text-violet-300" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold">Build by voice</h3>
                    <Badge>New</Badge>
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">
                    Describe what you want. The Setup Assistant picks a
                    template, sets the greeting, voice, and prompt for you —
                    all by conversation. About 90 seconds.
                  </p>
                  <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                    <li>✓ No forms</li>
                    <li>✓ Voice + text hybrid — type if your mic is off</li>
                    <li>✓ Live preview of the agent as it&apos;s built</li>
                  </ul>
                </div>
              </div>
            </CardContent>
          </Card>
        </Link>

        <Link href="/dashboard/agents/new?mode=form" className="block group">
          <Card className="h-full hover:border-cyan-500/60 transition-colors cursor-pointer">
            <CardContent className="pt-6">
              <div className="flex items-start gap-3">
                <div className="h-12 w-12 rounded-lg bg-gradient-to-br from-cyan-500/20 to-emerald-400/20 flex items-center justify-center shrink-0">
                  <FileEdit className="h-6 w-6 text-cyan-300" />
                </div>
                <div className="flex-1">
                  <h3 className="font-semibold">Build with a form</h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    Direct control over every field. Best when you already
                    know what you want or you&apos;re scripting an agent.
                  </p>
                  <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                    <li>✓ All fields editable at once</li>
                    <li>✓ Pick providers, models, voices manually</li>
                    <li>✓ Set API keys, channels, MCP — full surface</li>
                  </ul>
                </div>
              </div>
            </CardContent>
          </Card>
        </Link>
      </div>

      <p className="mt-6 text-center text-xs text-muted-foreground">
        Already have a template in mind? Skip both and visit{" "}
        <Link href="/dashboard/templates" className="text-violet-300 hover:underline">
          Templates
        </Link>{" "}
        to one-click instantiate.
      </p>
    </div>
  );
}

// Tiny inline pill used in the chooser. Avoids importing the full Badge UI.
function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider font-bold bg-violet-500/20 text-violet-200 border border-violet-500/40">
      {children}
    </span>
  );
}

// ──────────────────────────────────────────────────────────────────────
// FormFlow — unchanged from the pre-Session-10 page. Kept verbatim so
// users with existing bookmarks (`?mode=form`) get the same experience.
// ──────────────────────────────────────────────────────────────────────

function FormFlow() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "My voice agent",
    description: "",
    llm_provider: "byteplus",
    llm_model: "",
    stt_provider: "byteplus",
    tts_provider: "byteplus",
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
      router.push(`/dashboard/agents/edit?id=${a.id}`);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container py-8 max-w-3xl">
      <Link
        href="/dashboard/agents/new"
        className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back — pick a different setup mode
      </Link>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-violet-300" />
            New agent (form mode)
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
              <Select
                value={form.voice_language}
                onChange={(e) => set("voice_language", e.target.value)}
              >
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
              <Select
                value={form.llm_provider}
                onChange={(e) => set("llm_provider", e.target.value)}
              >
                <option value="byteplus">BytePlus (Seed)</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="gemini">Gemini</option>
                <option value="deepseek">DeepSeek</option>
              </Select>
            </div>
            <div>
              <Label>STT provider</Label>
              <Select
                value={form.stt_provider}
                onChange={(e) => set("stt_provider", e.target.value)}
              >
                <option value="byteplus">BytePlus (Seed ASR)</option>
                <option value="deepgram">Deepgram</option>
                <option value="assemblyai">AssemblyAI</option>
                <option value="whisper">Whisper</option>
              </Select>
            </div>
            <div>
              <Label>TTS provider</Label>
              <Select
                value={form.tts_provider}
                onChange={(e) => set("tts_provider", e.target.value)}
              >
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
