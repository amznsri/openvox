"use client";

/**
 * SetupAssistant — voice + text hybrid agent-creation flow.
 *
 * Two columns:
 *   LEFT  — chat: mic-toggle button, text input, scrolling transcript.
 *           Voice routes through /ws/voice (PCM in/out). Text routes
 *           through /api/v1/agents/{id}/turn (REST round-trip). Both
 *           drive the *same* Setup Assistant agent, and both write to
 *           the same Agent.channels.setup_state — so a user can speak
 *           one turn and type the next without losing draft state.
 *   RIGHT — live preview: SWR-polled view of the draft agent the
 *           Setup Assistant is building. Updates as each skill call
 *           lands.
 *
 * Lifecycle:
 *   1. Mount → resolve the canonical Setup Assistant agent via
 *      /api/v1/templates/setup-assistant/singleton.
 *   2. On first user input (voice or text), the assistant's greeting
 *      surfaces as a "system has spoken" line.
 *   3. Once any skill_result lands a draft_agent_id (we poll the
 *      assistant agent every 2s for `channels.setup_state.draft_agent_id`),
 *      the right pane swaps from a placeholder to the draft's full
 *      JSON config.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Mic,
  MicOff,
  Send,
  Square,
  Wand2,
} from "lucide-react";

import { api, wsUrl, type Agent } from "@/lib/api";
import { AudioPlaybackQueue, captureMicrophone } from "@/lib/voice/audio";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

type Line = {
  role: "user" | "assistant" | "skill" | "system";
  text: string;
  pending?: boolean;
};

export function SetupAssistant() {
  // ── Singleton resolution ─────────────────────────────────────────
  // Resolved on mount; the call is idempotent so a re-mount is safe.
  const { data: assistantAgent } = useSWR<Agent>(
    "setup-assistant-singleton",
    () => api.setupAssistantSingleton(),
    { revalidateOnFocus: false },
  );

  // ── Chat state ───────────────────────────────────────────────────
  const [lines, setLines] = useState<Line[]>([]);
  const [textInput, setTextInput] = useState("");
  const [textBusy, setTextBusy] = useState(false);

  // ── Voice state ──────────────────────────────────────────────────
  const [micState, setMicState] = useState<"idle" | "connecting" | "listening" | "speaking" | "error">("idle");
  const wsRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<{ stop: () => void } | null>(null);
  const playerRef = useRef<AudioPlaybackQueue | null>(null);

  // Auto-follow scroll. `scrollRef` is the transcript container;
  // `wasAtBottomRef` tracks whether the user is parked at the
  // bottom so we don't yank them back when they've scrolled up to
  // read an earlier message. Standard chat-UI affordance.
  const scrollRef = useRef<HTMLDivElement>(null);
  const wasAtBottomRef = useRef(true);
  function onTranscriptScroll() {
    const el = scrollRef.current;
    if (!el) return;
    // 50px tolerance — touch / momentum scrolls overshoot a bit;
    // treating "within 50px of bottom" as "at the bottom" avoids
    // breaking the follow when the user is genuinely at the end.
    wasAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
  }
  useEffect(() => {
    if (!wasAtBottomRef.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  // ── Live preview ─────────────────────────────────────────────────
  // Poll the assistant agent so we can read draft_agent_id out of its
  // channels.setup_state. The draft agent itself is then SWR'd via
  // its own key — same cache used by the dashboard agent detail page,
  // so changes the assistant makes are visible everywhere instantly.
  const { data: refreshedAssistant } = useSWR<Agent>(
    assistantAgent ? `setup-assistant-state-${assistantAgent.id}` : null,
    () => api.getAgent(assistantAgent!.id),
    { refreshInterval: 2000 },
  );
  const draftId: string =
    (((refreshedAssistant?.channels as any) || {}).setup_state || {})
      .draft_agent_id || "";
  const { data: draftAgent } = useSWR<Agent>(
    draftId ? `agent-${draftId}` : null,
    () => api.getAgent(draftId),
    { refreshInterval: draftId ? 2000 : 0 },
  );

  // ── History snapshot for text turns ──────────────────────────────
  // The /agents/{id}/turn route is stateless — we send the full
  // history each call so the LLM sees the thread. We rebuild from
  // `lines`, filtering out system + pending markers.
  function historyForTurn(): { role: string; content: string }[] {
    return lines
      .filter((l) => (l.role === "user" || l.role === "assistant") && !l.pending && l.text)
      .map((l) => ({ role: l.role, content: l.text }));
  }

  // ── Voice barge-in: browser-native stop-word listener ────────────
  // While the assistant is speaking, a SECOND, independent listener
  // runs in the browser (the page's own webkitSpeechRecognition) and
  // watches for short stop words. This is deliberately separate from
  // the WS-backed STT pipeline because:
  //   * WS STT is paused server-side while the assistant talks
  //     (turn-based design — see orchestrator._listen_one_turn).
  //   * Browser AEC isn't perfect, so server-side VAD can fail to
  //     see a "speech_start" transition when the user joins.
  //   * Web SpeechRecognition can run continuously and reliably
  //     match a tiny vocabulary (we only care about ~6 words).
  //
  // Caveats:
  //   * Webkit-prefixed; available in Chrome/Edge/Safari, NOT
  //     Firefox. We feature-detect and gracefully no-op elsewhere.
  //   * The recogniser is a SECOND mic claim — the browser merges
  //     them. Tested fine in Chrome 120+; if it conflicts we can
  //     fall back to button-only.
  //   * We use `lang="en-US"` because our default agent is English.
  //     A future fix: bind to the agent's voice_language.
  useEffect(() => {
    // Only active when the assistant is actually talking — no point
    // burning a mic claim otherwise.
    if (micState !== "speaking") return;
    const W = window as unknown as {
      webkitSpeechRecognition?: new () => any;
      SpeechRecognition?: new () => any;
    };
    const Recog = W.SpeechRecognition || W.webkitSpeechRecognition;
    if (!Recog) {
      // Firefox or older browser — fall back to the Stop button alone.
      return;
    }
    // Match short, clear interrupt words plus a couple of natural
    // phrases. All lowercased + boundary-checked so "stopwatch"
    // doesn't fire it. CJK variants included so a Mandarin user can
    // also interrupt: 停 / 停下 / 暂停.
    const STOP_PATTERNS = [
      /\bstop\b/i,
      /\bpause\b/i,
      /\bwait\b/i,
      /\bhalt\b/i,
      /\bcancel\b/i,
      /\bquiet\b/i,
      /\bhold on\b/i,
      /\bbe quiet\b/i,
      /停下?/,
      /暂停/,
    ];

    const r = new Recog();
    r.lang = "en-US";
    r.continuous = true;
    r.interimResults = true;
    r.maxAlternatives = 1;

    let triggered = false;
    r.onresult = (ev: any) => {
      if (triggered) return;
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const transcript: string = ev.results[i][0].transcript || "";
        for (const pat of STOP_PATTERNS) {
          if (pat.test(transcript)) {
            triggered = true;
            try {
              r.stop();
            } catch {
              /* stopping a half-started recogniser throws; ignore */
            }
            sendInterrupt("voice");
            return;
          }
        }
      }
    };
    r.onerror = () => {
      // Common: "no-speech" timeout, "aborted" when we unmount.
      // Both benign — we don't surface them.
    };
    try {
      r.start();
    } catch {
      // start() can throw if a previous instance is still active or
      // the user has denied mic permission. Either way, button stays.
      return;
    }
    return () => {
      try {
        r.stop();
      } catch {
        /* idempotent */
      }
    };
    // sendInterrupt is stable across re-renders (no deps); we only
    // want to re-run when the speaking state itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [micState]);

  // ── Aggressive lifecycle cleanup ─────────────────────────────────
  // Without this, an open mic + WS keeps streaming background audio
  // even after the user navigates away. BytePlus STT transcribes
  // ambient noise as garbage utterances, the LLM dutifully responds,
  // and TTS plays "every few seconds" with no apparent trigger.
  // Cleanup must fire on unmount AND on tab-visibility-change AND on
  // page-hide so we cover every navigation path.
  useEffect(() => {
    function teardown() {
      captureRef.current?.stop();
      captureRef.current = null;
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        try { wsRef.current.send(JSON.stringify({ type: "end" })); } catch { /* socket dying */ }
        wsRef.current.close();
      }
      wsRef.current = null;
      playerRef.current?.close?.();
      playerRef.current = null;
      setMicState("idle");
    }
    const onHide = () => {
      if (document.visibilityState === "hidden") teardown();
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", teardown);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", teardown);
      teardown();
    };
  }, []);

  // ── Voice WS plumbing ────────────────────────────────────────────
  async function toggleMic() {
    if (!assistantAgent) return;
    if (micState !== "idle" && micState !== "error") {
      stopVoice();
      return;
    }
    setMicState("connecting");
    try {
      playerRef.current = new AudioPlaybackQueue();
      const ws = new WebSocket(wsUrl("/ws/voice"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = async () => {
        ws.send(
          JSON.stringify({
            type: "start",
            agent_id: assistantAgent.id,
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
          handleEvent(JSON.parse(e.data));
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
    setMicState("idle");
  }

  // ── Barge-in: user-driven interrupt ──────────────────────────────
  // Three trigger paths feed this:
  //   1. The "Stop talking" button while micState === "speaking".
  //   2. The browser-native SpeechRecognition listener (useEffect
  //      below) when the user says a stop word ("stop", "pause",
  //      "wait", "halt", "cancel").
  //   3. (future) server-side VAD speech_start — already handled in
  //      the orchestrator; no client change needed.
  //
  // Both paths do the same two things:
  //   - Tell the backend to abort the in-flight TTS stream (so it
  //     doesn't keep sending more audio frames).
  //   - Drain the local AudioPlaybackQueue so whatever's already in
  //     flight stops in this browser tab immediately (no waiting
  //     for the ws round-trip).
  function sendInterrupt(source: "button" | "voice") {
    // 1. Server: stop synthesising / streaming.
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt", source }));
    }
    // 2. Client: silence whatever's already queued in the player.
    playerRef.current?.stopAll?.();
    // 3. Visible feedback so the user knows it worked.
    setLines((ls) => [...ls, { role: "system", text: `[stopped — ${source}]` }]);
    setMicState("listening");
  }

  function handleEvent(ev: any) {
    const t = ev.type;
    if (t === "user_partial") {
      setLines((ls) => {
        const last = ls[ls.length - 1];
        if (last?.role === "user" && last.pending) {
          return [...ls.slice(0, -1), { ...last, text: ev.text }];
        }
        return [...ls, { role: "user", text: ev.text, pending: true }];
      });
    } else if (t === "user_final") {
      setLines((ls) => {
        const last = ls[ls.length - 1];
        if (last?.role === "user" && last.pending) {
          return [...ls.slice(0, -1), { role: "user", text: ev.text }];
        }
        return [...ls, { role: "user", text: ev.text }];
      });
    } else if (t === "assistant_token") {
      setLines((ls) => {
        const last = ls[ls.length - 1];
        if (last?.role === "assistant" && last.pending) {
          return [...ls.slice(0, -1), { ...last, text: last.text + ev.text }];
        }
        return [...ls, { role: "assistant", text: ev.text, pending: true }];
      });
    } else if (t === "assistant_done") {
      setLines((ls) => {
        const last = ls[ls.length - 1];
        if (last?.role === "assistant" && last.pending) {
          return [...ls.slice(0, -1), { role: "assistant", text: ev.text || last.text }];
        }
        return ls;
      });
      // Trigger an SWR refresh so the live preview updates after the
      // skill calls that fired during this turn land.
      if (assistantAgent) globalMutate(`setup-assistant-state-${assistantAgent.id}`);
    } else if (t === "skill_call") {
      setLines((ls) => [
        ...ls,
        { role: "skill", text: `→ ${ev.text}(${JSON.stringify(ev.args ?? {})})` },
      ]);
    } else if (t === "skill_result") {
      setLines((ls) => [
        ...ls,
        { role: "skill", text: `← ${ev.text}: ${JSON.stringify(ev.output ?? {})}` },
      ]);
    } else if (t === "interrupt") {
      playerRef.current?.stopAll?.();
    } else if (t === "error" || t === "tts_error") {
      setLines((ls) => [...ls, { role: "system", text: `[${t}] ${ev.text || ""}` }]);
    }
  }

  // ── Text-turn plumbing ───────────────────────────────────────────
  async function sendText() {
    if (!assistantAgent) return;
    const msg = textInput.trim();
    if (!msg || textBusy) return;
    setTextInput("");
    const history = historyForTurn();
    setLines((ls) => [...ls, { role: "user", text: msg }, { role: "assistant", text: "", pending: true }]);
    setTextBusy(true);
    try {
      const r = await api.agentTurn(assistantAgent.id, msg, history);
      setLines((ls) => {
        // Replace the pending assistant placeholder with the final text.
        const out = [...ls];
        if (out[out.length - 1]?.role === "assistant" && out[out.length - 1].pending) {
          out[out.length - 1] = { role: "assistant", text: r.text };
        } else {
          out.push({ role: "assistant", text: r.text });
        }
        // Inject any skill call/result events between the user line
        // and the assistant line so the timeline reads correctly.
        const skillLines: Line[] = [];
        for (const e of r.events) {
          if (e.type === "skill_call") {
            skillLines.push({ role: "skill", text: `→ ${e.name}(${JSON.stringify(e.args ?? {})})` });
          } else if (e.type === "skill_result") {
            skillLines.push({ role: "skill", text: `← ${e.name}: ${JSON.stringify(e.output ?? {})}` });
          }
        }
        // Splice skill lines before the assistant message.
        return out.slice(0, -1).concat(skillLines, out.slice(-1));
      });
      // Refresh assistant state so the right pane reflects the new draft id.
      globalMutate(`setup-assistant-state-${assistantAgent.id}`);
    } catch (e: any) {
      setLines((ls) => [
        ...ls.slice(0, -1),
        { role: "system", text: `[error] ${e?.message || e}` },
      ]);
    } finally {
      setTextBusy(false);
    }
  }

  function onTextKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendText();
    }
  }

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div className="container py-6 max-w-7xl">
      <Link
        href="/dashboard/agents/new"
        className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back — pick a different setup mode
      </Link>

      <div className="mb-4 flex items-center gap-2">
        <Wand2 className="h-5 w-5 text-violet-300" />
        <h1 className="text-2xl font-bold">Voice setup</h1>
        <Badge variant="default">beta</Badge>
      </div>
      <p className="text-sm text-muted-foreground mb-6">
        Tell the assistant what kind of agent you want. It picks a template,
        names it, sets the greeting + voice, and walks you through what&apos;s
        left to fill in by clicking. Speak via the mic, or type — both work
        and they share state.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* LEFT: chat panel */}
        <Card className="min-h-[60vh] flex flex-col">
          <CardContent className="flex flex-col flex-1 pt-6 gap-3">
            <div
              ref={scrollRef}
              onScroll={onTranscriptScroll}
              className="flex-1 overflow-y-auto space-y-2 max-h-[55vh]"
              id="setup-transcript"
            >
              {!assistantAgent ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading Setup Assistant…
                </div>
              ) : lines.length === 0 ? (
                <div className="text-sm text-muted-foreground py-8 px-2">
                  {assistantAgent.greeting}
                </div>
              ) : (
                lines.map((l, i) => (
                  <TranscriptLine key={i} line={l} />
                ))
              )}
            </div>

            {/* Composer.
                When the assistant is speaking, the leftmost button
                turns into a red "Stop" — clicking it (or saying
                "stop"/"pause"/"wait"/"halt"/"cancel", picked up by
                the browser-native listener below) cuts the in-flight
                TTS immediately. */}
            <div className="border-t border-border/60 pt-3 flex items-center gap-2">
              {micState === "speaking" ? (
                <Button
                  variant="danger"
                  size="icon"
                  onClick={() => sendInterrupt("button")}
                  title='Stop the assistant — or just say "stop"'
                >
                  <Square className="h-4 w-4 fill-current" />
                </Button>
              ) : (
                <Button
                  variant={micState === "listening" ? "danger" : "outline"}
                  size="icon"
                  onClick={toggleMic}
                  disabled={!assistantAgent}
                  title={micState === "listening" ? "Stop microphone" : "Start microphone"}
                >
                  {micState === "connecting" ? <Loader2 className="h-4 w-4 animate-spin" />
                    : micState === "listening" ? <MicOff className="h-4 w-4" />
                    : <Mic className="h-4 w-4" />}
                </Button>
              )}
              <input
                value={textInput}
                onChange={(e) => setTextInput(e.target.value)}
                onKeyDown={onTextKey}
                placeholder="Or type — e.g. 'I want to book salon appointments'"
                className="flex-1 h-9 rounded-md border border-border/60 bg-input/40 px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                disabled={!assistantAgent || textBusy}
              />
              <Button variant="gradient" size="sm" onClick={sendText} disabled={!assistantAgent || textBusy || !textInput.trim()}>
                {textBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {micState === "listening" && "🎙 Listening — speak naturally."}
              {micState === "speaking" && '🔊 Assistant is responding — tap ⏹ or say "stop" to interrupt.'}
              {micState === "connecting" && "Connecting…"}
              {micState === "error" && "Mic error — try again or use text."}
              {micState === "idle" && "Mic off — click the mic or type. Voice and text share the same conversation."}
            </p>
          </CardContent>
        </Card>

        {/* RIGHT: live preview */}
        <Card className="min-h-[60vh]">
          <CardContent className="pt-6 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm uppercase tracking-wider text-muted-foreground">
                Draft preview
              </h3>
              {draftAgent && (
                <Badge variant={draftAgent.status === "published" ? "success" : "default"}>
                  {draftAgent.status}
                </Badge>
              )}
            </div>
            {!draftId ? (
              <div className="text-sm text-muted-foreground py-8 text-center">
                Once you describe what you want, the assistant will create a
                draft agent. You&apos;ll see its config build up here in
                real time.
              </div>
            ) : !draftAgent ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            ) : (
              <div className="space-y-3 text-sm">
                <DraftField label="Name" value={draftAgent.name} />
                <DraftField label="Template" value={draftAgent.template_id || "—"} mono />
                <DraftField label="Greeting" value={draftAgent.greeting} multi />
                <DraftField label="System prompt" value={draftAgent.system_prompt} multi clamp />
                <DraftField label="Voice" value={draftAgent.voice_id} mono />
                <DraftField label="Language" value={draftAgent.voice_language} mono />
                <DraftField
                  label="Skills"
                  value={(draftAgent.skills || []).join(", ") || "—"}
                  mono
                  clamp
                />
                {draftAgent.status === "published" ? (
                  <div className="pt-3 border-t border-border/60">
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-200 text-sm px-3 py-2 flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 shrink-0" />
                      Live — open the agent to test it.
                    </div>
                    <Link href={`/dashboard/agents/${draftAgent.id}`}>
                      <Button variant="gradient" size="sm" className="mt-2 w-full">
                        Open {draftAgent.name}
                      </Button>
                    </Link>
                  </div>
                ) : (
                  <div className="pt-3 border-t border-border/60 flex gap-2">
                    <Link href={`/dashboard/agents/${draftAgent.id}`} className="flex-1">
                      <Button variant="outline" size="sm" className="w-full">
                        Open in form editor
                      </Button>
                    </Link>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ── Small helper components ────────────────────────────────────────

function TranscriptLine({ line }: { line: Line }) {
  const colors: Record<Line["role"], string> = {
    user: "text-cyan-300",
    assistant: "text-violet-300",
    skill: "text-amber-300",
    system: "text-rose-300",
  };
  return (
    <div
      className={`flex gap-3 ${line.pending ? "opacity-60" : ""}`}
    >
      <span
        className={`text-[10px] uppercase tracking-wider font-bold shrink-0 w-16 pt-0.5 ${colors[line.role]}`}
      >
        {line.role}
      </span>
      <span className="text-sm flex-1 break-words whitespace-pre-wrap">{line.text}</span>
    </div>
  );
}

function DraftField({
  label,
  value,
  mono,
  multi,
  clamp,
}: {
  label: string;
  value: string;
  mono?: boolean;
  multi?: boolean;
  clamp?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div
        className={`mt-0.5 ${mono ? "font-mono text-xs" : "text-sm"} ${multi ? "whitespace-pre-wrap" : ""} ${clamp ? "line-clamp-3" : ""}`}
      >
        {value || "—"}
      </div>
    </div>
  );
}
