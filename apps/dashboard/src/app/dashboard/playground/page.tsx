"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ArrowRight, Bot, FileUp, Loader2, Mic as MicIcon, Search, Send, Sparkles, Square, Upload, User2 } from "lucide-react";

import { api, wsUrl, type Agent, type Provider } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MicButton, type MicState } from "@/components/playground/MicButton";
import { AudioPlaybackQueue, captureMicrophone } from "@/lib/voice/audio";

type Line = { role: "user" | "assistant" | "skill" | "system"; text: string; pending?: boolean };

// Default export wrapped in <Suspense> so useSearchParams works under
// Next.js `output: 'export'` (static export). Same pattern as
// agents/edit/page.tsx and integrations/page.tsx — see CLAUDE.md §8
// #93 for the build-time error this prevents.
export default function PlaygroundPageWrapper() {
  return (
    <Suspense fallback={<div className="container py-12 text-muted-foreground">Loading…</div>}>
      <PlaygroundPage />
    </Suspense>
  );
}

function PlaygroundPage() {
  const { data: agents = [] } = useSWR<Agent[]>("agents", () => api.listAgents());
  const { data: providers = [] } = useSWR<Provider[]>("providers", () => api.listProviders());

  // Pre-select an agent when the page is opened via "Test" buttons
  // (Agents list, Agent edit page, voice/typed "test <name>" command).
  // All four entry points pass `?agent=<id>` in the URL; without this,
  // the Configuration card stuck on "Ad-hoc (use settings below)"
  // regardless of which agent the user had just clicked Test for.
  const searchParams = useSearchParams();
  const agentParam = searchParams.get("agent") ?? "";
  const [selectedAgent, setSelectedAgent] = useState<string>(agentParam);
  // The query param may arrive BEFORE the agents SWR has resolved; we
  // accept it eagerly and let the "apply settings" effect below run
  // once `agents` loads. If the URL param is later changed while the
  // page is open (e.g. back-button to a previous Test click), sync
  // the dropdown to match.
  useEffect(() => {
    if (agentParam) setSelectedAgent(agentParam);
  }, [agentParam]);
  const [systemPrompt, setSystemPrompt] = useState(
    "You are a helpful voice assistant. Keep responses under 2 sentences.",
  );
  const [llmProvider, setLlmProvider] = useState("byteplus");
  // Empty string is the canonical "use settings default" sentinel —
  // BytePlus / OpenAI providers resolve it to their configured model
  // (settings.byteplus_llm_model = "seed-2-0-pro-260328" by default).
  // The previous hardcoded "doubao-seed-1.6-250615" was stale and
  // didn't exist on the user's key — see CLAUDE.md §8 #45.
  const [llmModel, setLlmModel] = useState("");
  const [sttProvider, setSttProvider] = useState("byteplus");
  const [ttsProvider, setTtsProvider] = useState("byteplus");

  const [lines, setLines] = useState<Line[]>([]);
  const [textInput, setTextInput] = useState("");
  const [textPending, setTextPending] = useState(false);

  const [micState, setMicState] = useState<MicState>("idle");
  const wsRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<{ stop: () => void } | null>(null);
  const playerRef = useRef<AudioPlaybackQueue | null>(null);

  // Apply selected agent's settings to the controls.
  useEffect(() => {
    if (!selectedAgent) return;
    const a = agents.find((x) => x.id === selectedAgent);
    if (!a) return;
    setSystemPrompt(a.system_prompt);
    setLlmProvider(a.llm_provider);
    setLlmModel(a.llm_model);
    setSttProvider(a.stt_provider);
    setTtsProvider(a.tts_provider);
  }, [selectedAgent, agents]);

  function provFor(type: "llm" | "stt" | "tts") {
    return providers.filter((p) => p.type === type);
  }

  // ── Voice mode ────────────────────────────────────────────────
  async function startVoice() {
    if (micState !== "idle" && micState !== "error") {
      // toggle off
      stopVoice();
      return;
    }
    setMicState("connecting");
    setLines([]);
    try {
      playerRef.current = new AudioPlaybackQueue();
      const ws = new WebSocket(wsUrl("/ws/voice"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = async () => {
        ws.send(
          JSON.stringify({
            type: "start",
            agent_id: selectedAgent || undefined,
            system_prompt: systemPrompt,
            llm_provider: llmProvider,
            llm_model: llmModel,
            stt_provider: sttProvider,
            tts_provider: ttsProvider,
            sample_rate: 16000,
          }),
        );
        setMicState("listening");
        captureRef.current = await captureMicrophone(
          (frame) => {
            if (ws.readyState === WebSocket.OPEN) ws.send(frame.buffer);
          },
          { sampleRate: 16000 },
        );
      };

      ws.onmessage = (e) => {
        if (typeof e.data === "string") {
          const ev = JSON.parse(e.data) as Record<string, unknown>;
          handleEvent(ev);
        } else if (e.data instanceof ArrayBuffer) {
          playerRef.current?.enqueuePcm16(e.data, 24000);
          setMicState("speaking");
        }
      };

      ws.onerror = () => setMicState("error");
      ws.onclose = () => {
        if (micState !== "error") setMicState("idle");
      };
    } catch (err) {
      console.error(err);
      setMicState("error");
    }
  }

  function stopVoice() {
    captureRef.current?.stop();
    captureRef.current = null;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "end" }));
      wsRef.current.close();
    }
    wsRef.current = null;
    playerRef.current?.close();
    playerRef.current = null;
    setMicState("idle");
  }

  // Aggressive lifecycle cleanup — if the user navigates away (tab
  // switch, page hide, component unmount) we MUST stop the mic and
  // close the WebSocket. Otherwise the open mic keeps streaming
  // background audio, BytePlus STT transcribes it as garbage
  // utterances, and the LLM responds — playing TTS audio "every few
  // seconds" with no apparent trigger from the user's side.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") stopVoice();
    };
    const onPagehide = () => stopVoice();
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onPagehide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onPagehide);
      stopVoice();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleEvent(ev: Record<string, unknown>) {
    const type = ev.type as string;
    const text = (ev.text as string) || "";
    if (type === "user_partial") {
      // Barge-in detection. If the assistant's audio queue is
      // currently playing (or has pending buffers), the user
      // starting to speak means INTERRUPT, not "wait until current
      // utterance finishes".
      //
      // Two parallel actions:
      //   1. LOCAL: drain the playback queue immediately. The server
      //      may already have synthesised 20-30 seconds of audio
      //      ahead of realtime (BytePlus TTS streams faster than
      //      audio plays); without this the user hears the rest
      //      even though the server stops generating new chunks.
      //   2. REMOTE: send {type:"interrupt"} to the WS. The
      //      orchestrator's interrupt() sets _cancel_tts, which the
      //      _speak() loop checks each chunk — kills in-flight TTS
      //      generation on the server side too.
      //
      // The user-spotted bug this fixes: with BytePlus pipeline,
      // mid-response "Stop" was transcribed (we see USER: Stop in
      // the transcript) and the LLM produced a new "Got it" turn,
      // but the dashboard kept playing audio for the PREVIOUS
      // turn's pre-buffered bullets. Reported 2026-05-27.
      //
      // Why on user_partial (not user_final): partial fires within
      // ~150ms of speech start; final waits for end-of-utterance
      // (which can be 1-2 seconds later). Cutting audio at partial
      // matches human conversational barge-in timing.
      if (playerRef.current?.isPlaying()) {
        playerRef.current.stopAll();
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(
            JSON.stringify({ type: "interrupt", source: "client-stt-partial" })
          );
        }
      }
      setLines((ls) => upsertPending(ls, "user", text));
    } else if (type === "user_final") {
      // Defence-in-depth: if the STT provider doesn't emit
      // user_partial (some adapters only fire user_final on
      // end-of-utterance), the barge-in branch above never ran.
      // Repeat the check here so audio still drains, just with
      // slightly more delay than the partial-driven path.
      // The stopAll() is a no-op if the queue is already empty
      // (e.g. partial already fired this turn), so it's safe to
      // call unconditionally.
      if (playerRef.current?.isPlaying()) {
        playerRef.current.stopAll();
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(
            JSON.stringify({ type: "interrupt", source: "client-stt-final" })
          );
        }
      }
      setLines((ls) => finalisePending(ls, "user", text));
    } else if (type === "assistant_token") {
      setLines((ls) => appendAssistantToken(ls, text));
    } else if (type === "assistant_done") {
      setLines((ls) => finaliseLastAssistant(ls));
      setMicState("listening");
    } else if (type === "skill_call") {
      setLines((ls) => [...ls, { role: "skill", text: `→ ${text}(${JSON.stringify((ev as any).args ?? {})})` }]);
    } else if (type === "skill_result") {
      setLines((ls) => [...ls, { role: "skill", text: `← ${text}: ${JSON.stringify((ev as any).output ?? (ev as any).data ?? {})}` }]);
    } else if (type === "interrupt") {
      playerRef.current?.stopAll();
    } else if (type === "tts_error") {
      // Soft failure: text response continues but no audio. Surface this
      // in the transcript so the user understands why it's silent.
      const hint = ((ev as any).hint as string) || "";
      const msg = `TTS unavailable — ${text}${hint ? `\n${hint}` : ""}`;
      setLines((ls) => [...ls, { role: "system", text: msg }]);
    } else if (type === "error") {
      console.error("server error", ev);
      setLines((ls) => [...ls, { role: "system", text: `Error: ${text || "see core logs"}` }]);
      setMicState("error");
    }
  }

  // ── Text mode ─────────────────────────────────────────────────
  async function sendText() {
    if (!textInput.trim() || textPending) return;
    const userMsg = textInput.trim();
    setLines((ls) => [...ls, { role: "user", text: userMsg }]);
    setTextInput("");
    setTextPending(true);
    setLines((ls) => [...ls, { role: "assistant", text: "", pending: true }]);
    try {
      await api.textChat(
        {
          provider: llmProvider,
          model: llmModel,
          system: systemPrompt,
          user: userMsg,
          // Surfaces this turn on Observability when an agent is picked.
          agent_id: selectedAgent || undefined,
        },
        (tok) => {
          setLines((ls) => {
            const last = ls[ls.length - 1];
            if (!last || last.role !== "assistant") return ls;
            const next = ls.slice(0, -1);
            return [...next, { ...last, text: last.text + tok }];
          });
        },
      );
    } catch (e) {
      setLines((ls) => [...ls, { role: "assistant", text: `Error: ${(e as Error).message}` }]);
    } finally {
      setLines((ls) => ls.map((l, i) => (i === ls.length - 1 ? { ...l, pending: false } : l)));
      setTextPending(false);
    }
  }

  return (
    <div className="container py-8 grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
      {/* Left: configuration */}
      <Card className="self-start sticky top-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-300" />
            Configuration
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Agent</Label>
            <Select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}>
              <option value="">Ad-hoc (use settings below)</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </div>
          {/*
            S2S-mode banner. Pulled from the selected agent's
            `s2s_provider` field — populated only when the user
            opted into S2S on the agent edit page. We don't
            re-implement the toggle here; the playground is for
            EXERCISING the agent's saved config, not editing it.

            Showing the banner above the system prompt / providers
            grid (rather than below) means the user sees "this is
            S2S mode" before scanning the fields that LOOK like
            they're active. Without this header the saved BytePlus
            values in the LLM / STT / TTS dropdowns read as
            "BytePlus is being used right now", when in reality
            the WS voice route ignores them entirely in S2S mode.
          */}
          {(() => {
            const a = agents.find((x) => x.id === selectedAgent);
            const s2s = (a?.s2s_provider || "").trim();
            if (!s2s) return null;
            return (
              <div className="rounded-md border border-violet-500/30 bg-violet-500/5 px-3 py-2 text-xs text-violet-200/90 space-y-1">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-violet-300" />
                  <span className="font-semibold">
                    {s2s === "openai_realtime"
                      ? "S2S — OpenAI Realtime"
                      : `S2S — ${s2s}`}
                  </span>
                </div>
                <p>
                  Single-WS voice. The provider fields below are saved
                  fallback only and <em>not consulted</em> while S2S is
                  engaged. To change voice mode, open Agents → Edit.
                </p>
              </div>
            );
          })()}
          <div>
            <Label>System prompt</Label>
            <Textarea
              rows={5}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
            />
          </div>
          {(() => {
            const a = agents.find((x) => x.id === selectedAgent);
            const s2sActive = !!(a?.s2s_provider || "").trim();
            return (
              <div
                className={`grid grid-cols-2 gap-3 ${
                  s2sActive ? "opacity-50 pointer-events-none" : ""
                }`}
                aria-disabled={s2sActive}
              >
            <div>
              <Label>LLM</Label>
              <Select
                value={llmProvider}
                onChange={(e) => setLlmProvider(e.target.value)}
                disabled={s2sActive}
              >
                {provFor("llm").map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.available}>
                    {p.display_name}
                    {!p.available ? " (no key)" : ""}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Model</Label>
              <Input
                value={llmModel}
                onChange={(e) => setLlmModel(e.target.value)}
                disabled={s2sActive}
                // Leaving blank is the canonical "use the provider's
                // configured default" sentinel — surfaced as a
                // placeholder so users don't think the field is
                // broken when an agent has no explicit model set.
                placeholder="(use provider default from .env)"
              />
            </div>
            <div>
              <Label>STT</Label>
              <Select
                value={sttProvider}
                onChange={(e) => setSttProvider(e.target.value)}
                disabled={s2sActive}
              >
                {provFor("stt").map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.available}>
                    {p.display_name}
                    {!p.available ? " (no key)" : ""}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label>TTS</Label>
              <Select
                value={ttsProvider}
                onChange={(e) => setTtsProvider(e.target.value)}
                disabled={s2sActive}
              >
                {provFor("tts").map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.available}>
                    {p.display_name}
                    {!p.available ? " (no key)" : ""}
                  </option>
                ))}
              </Select>
            </div>
              </div>
            );
          })()}
        </CardContent>
      </Card>

      {/* Right: interactive playground */}
      <div className="space-y-4">
        <Tabs defaultValue="voice">
          <TabsList>
            <TabsTrigger value="voice">Voice</TabsTrigger>
            <TabsTrigger value="text">Text</TabsTrigger>
            <TabsTrigger value="file">Audio file</TabsTrigger>
            <TabsTrigger value="docs">Documents</TabsTrigger>
          </TabsList>

          <TabsContent value="voice">
            <Card>
              <CardContent className="pt-6">
                <div className="flex flex-col items-center gap-4">
                  <MicButton state={micState} onClick={startVoice} />
                  {micState === "listening" || micState === "speaking" ? (
                    <Button variant="ghost" size="sm" onClick={stopVoice}>
                      <Square className="h-3.5 w-3.5" />
                      End conversation
                    </Button>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="text">
            <Card>
              <CardContent className="pt-6 flex gap-2">
                <Input
                  placeholder="Type a message…"
                  value={textInput}
                  onChange={(e) => setTextInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendText()}
                />
                <Button onClick={sendText} disabled={textPending || !textInput.trim()}>
                  {textPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  Send
                </Button>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="file">
            <AudioFileTab onResult={(r) => setLines(_audioToLines(r))} />
          </TabsContent>

          <TabsContent value="docs">
            <DocumentTab agentId={selectedAgent} onAnswer={(q, a) => setLines(_docToLines(q, a))} />
          </TabsContent>
        </Tabs>

        {/* Transcript */}
        <Card className="min-h-[420px]">
          <CardHeader>
            <CardTitle>Transcript</CardTitle>
          </CardHeader>
          <CardContent>
            {lines.length === 0 ? (
              <div className="text-center py-16 text-muted-foreground text-sm">
                <ArrowRight className="h-6 w-6 mx-auto mb-3 text-violet-400" />
                Start a conversation. Mic input is streamed live to the agent — first audio
                response in under 300 ms when providers are configured.
              </div>
            ) : (
              <div className="space-y-3">
                {lines.map((l, i) => (
                  <div key={i} className="flex gap-3 animate-fade-in">
                    <div
                      className={`h-7 w-7 rounded-full flex items-center justify-center shrink-0 ${
                        l.role === "user"
                          ? "bg-cyan-500/20 text-cyan-300"
                          : l.role === "skill"
                            ? "bg-amber-500/20 text-amber-300"
                            : l.role === "system"
                              ? "bg-rose-500/20 text-rose-300"
                              : "bg-violet-500/20 text-violet-300"
                      }`}
                    >
                      {l.role === "user" ? (
                        <User2 className="h-3.5 w-3.5" />
                      ) : l.role === "skill" ? (
                        <Sparkles className="h-3.5 w-3.5" />
                      ) : l.role === "system" ? (
                        <span className="text-xs">!</span>
                      ) : (
                        <Bot className="h-3.5 w-3.5" />
                      )}
                    </div>
                    <div className="flex-1">
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-0.5">
                        {l.role}
                      </div>
                      <div className="text-sm whitespace-pre-wrap leading-relaxed">
                        {l.text || (l.pending ? "▍" : "")}
                        {l.pending && <span className="inline-block ml-1 animate-pulse">▍</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────────
function upsertPending(ls: Line[], role: Line["role"], text: string): Line[] {
  const last = ls[ls.length - 1];
  if (last && last.role === role && last.pending) {
    const next = ls.slice(0, -1);
    return [...next, { ...last, text }];
  }
  return [...ls, { role, text, pending: true }];
}

function finalisePending(ls: Line[], role: Line["role"], text: string): Line[] {
  const last = ls[ls.length - 1];
  if (last && last.role === role && last.pending) {
    const next = ls.slice(0, -1);
    return [...next, { ...last, text, pending: false }];
  }
  return [...ls, { role, text }];
}

function appendAssistantToken(ls: Line[], tok: string): Line[] {
  const last = ls[ls.length - 1];
  if (last && last.role === "assistant" && last.pending) {
    const next = ls.slice(0, -1);
    return [...next, { ...last, text: last.text + tok }];
  }
  return [...ls, { role: "assistant", text: tok, pending: true }];
}

function finaliseLastAssistant(ls: Line[]): Line[] {
  const last = ls[ls.length - 1];
  if (last && last.role === "assistant" && last.pending) {
    const next = ls.slice(0, -1);
    return [...next, { ...last, pending: false }];
  }
  return ls;
}

// ──────────────────────────────────────────────────────────────────────
// Audio file tab — upload an audio file, get transcript + sentiment +
// profanity. Routes through /api/v1/playground/audio_analyze.
// ──────────────────────────────────────────────────────────────────────

function AudioFileTab({ onResult }: { onResult: (r: import("@/lib/api").AudioAnalysis) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");

  async function go() {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.analyzeAudio(file, { sentiment: true, profanity: true });
      onResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardContent className="pt-6 space-y-3">
        <div className="text-sm text-muted-foreground">
          Upload an audio recording (mp3, wav, m4a, ogg, flac). It will be transcribed
          via BytePlus Seed ASR, then analysed for sentiment and profanity.
        </div>
        <label className="flex items-center gap-3 px-3 py-2 border border-dashed border-border/60 rounded-md cursor-pointer hover:border-primary/40">
          <Upload className="h-4 w-4 text-violet-300" />
          <span className="text-sm flex-1 truncate">
            {file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "Click to choose a file…"}
          </span>
          <input
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <div className="flex justify-end">
          <Button variant="gradient" onClick={go} disabled={busy || !file}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            Analyse
          </Button>
        </div>
        {error && <div className="text-xs text-rose-300">{error}</div>}
      </CardContent>
    </Card>
  );
}

function _audioToLines(r: import("@/lib/api").AudioAnalysis): Line[] {
  const out: Line[] = [];
  if (r.filename) {
    out.push({ role: "system", text: `Analysed ${r.filename} (${(r.duration_ms / 1000).toFixed(1)}s)` });
  }
  if (r.transcript) out.push({ role: "user", text: r.transcript });
  const parts: string[] = [];
  if (r.sentiment?.output) {
    const s = r.sentiment.output;
    parts.push(`Sentiment: **${s.label}** (${s.method}, conf ${s.confidence.toFixed(2)})`);
  }
  if (r.profanity?.output) {
    const p = r.profanity.output;
    parts.push(`Profanity: ${p.hits.length === 0 ? "clean" : p.hits.join(", ")} · severity ${p.severity.toFixed(2)}`);
  }
  if (parts.length) out.push({ role: "assistant", text: parts.join("\n\n") });
  return out;
}

// ──────────────────────────────────────────────────────────────────────
// Document tab — query the agent's uploaded document KB. Documents
// themselves are uploaded on the agent edit page (Documents tab).
// ──────────────────────────────────────────────────────────────────────

function DocumentTab({
  agentId,
  onAnswer,
}: {
  agentId: string;
  onAnswer: (q: string, a: import("@/lib/api").DocumentQueryResult) => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState("");

  // Recording state — kept in refs so we don't re-render on every chunk.
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // Single AudioPlaybackQueue we reuse so consecutive answers don't pile up.
  const playerRef = useRef<AudioPlaybackQueue | null>(null);

  function stopPlayback() {
    playerRef.current?.stopAll();
    setSpeaking(false);
  }

  async function ask(question: string) {
    if (!agentId) {
      setError("Pick an agent that has documents uploaded.");
      return;
    }
    if (!question.trim()) return;
    setBusy(true);
    setError("");
    stopPlayback();
    try {
      const r = await api.queryDocuments(agentId, question.trim(), 5);
      onAnswer(question.trim(), r);
      setQ("");

      // Speak the answer (best-effort — silently no-op if TTS fails).
      if (r.answer) {
        try {
          setSpeaking(true);
          if (!playerRef.current) playerRef.current = new AudioPlaybackQueue();
          const { audio, sampleRate } = await api.synthesize(r.answer);
          playerRef.current.enqueuePcm16(audio, sampleRate);
          // Best-effort indicator: clear after the audio's duration.
          const i16 = audio.byteLength / 2;
          const ms = Math.ceil((i16 / sampleRate) * 1000);
          setTimeout(() => setSpeaking(false), ms + 250);
        } catch (e) {
          setSpeaking(false);
          console.warn("TTS failed:", e);
        }
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Pick a MediaRecorder mime that pydub/ffmpeg can decode.
  function pickMime(): string {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    for (const m of candidates) {
      if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m)) return m;
    }
    return "";
  }

  async function startRecording() {
    setError("");
    stopPlayback();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
      const mime = pickMime();
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorderRef.current = rec;
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        chunksRef.current = [];
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        if (blob.size === 0) return;
        setTranscribing(true);
        try {
          const { transcript } = await api.transcribe(blob);
          if (transcript.trim()) {
            setQ(transcript);
            await ask(transcript);
          } else {
            setError("Couldn't hear anything — try again.");
          }
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setTranscribing(false);
        }
      };
      rec.start();
      setRecording(true);
    } catch (e) {
      setError(`Microphone unavailable: ${(e as Error).message}`);
      setRecording(false);
    }
  }

  function stopRecording() {
    setRecording(false);
    recorderRef.current?.stop();
    recorderRef.current = null;
  }

  return (
    <Card>
      <CardContent className="pt-6 space-y-3">
        <div className="text-sm text-muted-foreground">
          Ask a question about the documents uploaded to this agent. You can{" "}
          <span className="text-foreground">type</span> or{" "}
          <span className="text-foreground">tap the mic</span> — answers play back as voice.
          Manage uploads on the agent's edit page → Documents tab.
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="What does the contract say about renewal?"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask(q)}
            disabled={recording || transcribing || busy}
          />
          <Button
            variant={recording ? "danger" : "outline"}
            size="icon"
            onClick={recording ? stopRecording : startRecording}
            disabled={transcribing || busy}
            aria-label={recording ? "Stop recording" : "Record"}
            title={recording ? "Stop recording" : "Hold to ask by voice"}
          >
            {transcribing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : recording ? (
              <Square className="h-4 w-4" />
            ) : (
              <MicIcon className="h-4 w-4" />
            )}
          </Button>
          <Button variant="gradient" onClick={() => ask(q)} disabled={busy || !q.trim() || recording}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            Ask
          </Button>
        </div>
        <div className="flex items-center justify-between text-xs">
          <div className="text-muted-foreground">
            {recording && (
              <span className="inline-flex items-center gap-1.5 text-rose-300">
                <span className="inline-block h-2 w-2 rounded-full bg-rose-400 animate-pulse" />
                Recording — tap stop when you're done
              </span>
            )}
            {transcribing && <span className="text-muted-foreground">Transcribing…</span>}
            {speaking && (
              <button
                type="button"
                className="inline-flex items-center gap-1.5 text-violet-300 hover:text-violet-200"
                onClick={stopPlayback}
              >
                <span className="inline-block h-2 w-2 rounded-full bg-violet-400 animate-pulse" />
                Speaking — tap to mute
              </button>
            )}
          </div>
          {error && <div className="text-rose-300">{error}</div>}
        </div>
      </CardContent>
    </Card>
  );
}

function _docToLines(question: string, r: import("@/lib/api").DocumentQueryResult): Line[] {
  const out: Line[] = [{ role: "user", text: question }];
  if (r.note) {
    out.push({ role: "system", text: r.note });
    return out;
  }
  if (r.answer) out.push({ role: "assistant", text: r.answer });
  if (r.passages?.length) {
    out.push({
      role: "skill",
      text:
        "Retrieved:\n" +
        r.passages
          .map((p) => `• ${p.source} (p${p.page}, ${p.score.toFixed(2)}): ${p.snippet.slice(0, 120)}`)
          .join("\n"),
    });
  }
  return out;
}
