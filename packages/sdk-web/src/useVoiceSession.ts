/**
 * React hook glue around MicCapture / PcmPlayer / VoiceWS.
 *
 * The hook exposes the same lifecycle the dashboard playground uses
 * internally, so any React app gets the full feature set with zero
 * extra wiring:
 *
 *   - start()      open WS, mic, playback
 *   - stop()       drain & close everything
 *   - interrupt()  user barged in — flush playback + tell server
 *
 * State returned:
 *   status        "idle" | "connecting" | "live" | "ended" | "error"
 *   transcript    rolling list of {role, text}
 *   error         error message string when status === "error"
 *
 * We deliberately keep transcript as plain text — apps can render
 * skill calls / results / partials however they want by passing their
 * own `onEvent` callback to `useVoiceSession(options)`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { MicCapture, PcmPlayer } from "./audio";
import { VoiceEvent, VoiceWS, type StartArgs } from "./ws";

export type SessionStatus = "idle" | "connecting" | "live" | "ended" | "error";

export interface TranscriptLine {
  role: "user" | "assistant" | "skill";
  text: string;
  /** True while this line is still streaming (partial assistant or user). */
  pending?: boolean;
}

export interface UseVoiceSessionOptions {
  /**
   * WS URL prefix. We append `/ws/voice` ourselves. For local dev,
   * default to your gateway: `ws://localhost:3001`.
   */
  server: string;
  agentId: string;
  /**
   * Called for every event the server emits — useful for custom UIs
   * that want to render skill calls, partial transcripts, etc.
   */
  onEvent?: (ev: VoiceEvent) => void;
}

export function useVoiceSession(opts: UseVoiceSessionOptions) {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [error, setError] = useState<string>("");

  const wsRef = useRef<VoiceWS | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);

  // Clean up on unmount so navigating away doesn't leave the mic open.
  useEffect(() => () => { void stop(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleEvent = useCallback((ev: VoiceEvent) => {
    opts.onEvent?.(ev);
    switch (ev.type) {
      case "user_partial":
        setTranscript((ls) => {
          const last = ls[ls.length - 1];
          if (last?.role === "user" && last.pending) {
            return [...ls.slice(0, -1), { ...last, text: ev.text }];
          }
          return [...ls, { role: "user", text: ev.text, pending: true }];
        });
        break;
      case "user_final":
        setTranscript((ls) => {
          const last = ls[ls.length - 1];
          if (last?.role === "user" && last.pending) {
            return [...ls.slice(0, -1), { role: "user", text: ev.text }];
          }
          return [...ls, { role: "user", text: ev.text }];
        });
        break;
      case "assistant_token":
        setTranscript((ls) => {
          const last = ls[ls.length - 1];
          if (last?.role === "assistant" && last.pending) {
            return [...ls.slice(0, -1), { ...last, text: last.text + ev.text }];
          }
          return [...ls, { role: "assistant", text: ev.text, pending: true }];
        });
        break;
      case "assistant_done":
        setTranscript((ls) => {
          const last = ls[ls.length - 1];
          if (last?.role === "assistant" && last.pending) {
            return [...ls.slice(0, -1), { role: "assistant", text: ev.text || last.text }];
          }
          return ls;
        });
        break;
      case "audio":
        playerRef.current?.enqueue(ev.pcm, ev.sampleRate);
        break;
      case "interrupt":
        playerRef.current?.clear();
        break;
      case "skill_call":
        setTranscript((ls) => [
          ...ls,
          { role: "skill", text: `→ ${ev.text}(${JSON.stringify((ev as any).args ?? {})})` },
        ]);
        break;
      case "skill_result":
        setTranscript((ls) => [
          ...ls,
          { role: "skill", text: `← ${ev.text}: ${JSON.stringify((ev as any).output ?? {})}` },
        ]);
        break;
      case "tts_error":
      case "error":
        setError(ev.text || "session error");
        setStatus("error");
        break;
    }
  }, [opts]);

  const start = useCallback(async () => {
    if (wsRef.current) return; // already running
    setStatus("connecting");
    setError("");
    try {
      const ws = new VoiceWS();
      const mic = new MicCapture();
      const player = new PcmPlayer();
      wsRef.current = ws;
      micRef.current = mic;
      playerRef.current = player;

      const wsUrl = `${opts.server.replace(/^http/, "ws")}/ws/voice`;
      const args: StartArgs = { url: wsUrl, agentId: opts.agentId };
      await ws.connect(args, handleEvent);
      await player.ensureContext();
      await mic.start((pcm) => ws.pushAudio(pcm));
      setStatus("live");
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("error");
      await stop();
    }
  }, [opts.agentId, opts.server, handleEvent]);

  const stop = useCallback(async () => {
    try { await micRef.current?.stop(); } catch { /* ignore */ }
    try { await playerRef.current?.close(); } catch { /* ignore */ }
    try { wsRef.current?.end(); } catch { /* ignore */ }
    micRef.current = null;
    playerRef.current = null;
    wsRef.current = null;
    setStatus((s) => (s === "error" ? s : "ended"));
  }, []);

  const interrupt = useCallback(() => {
    playerRef.current?.clear();
    wsRef.current?.interrupt();
  }, []);

  return { status, transcript, error, start, stop, interrupt };
}
