/**
 * VoiceSession — drive the OpenVox WS pipeline from any TS environment.
 *
 * In Node:
 *
 *   import { VoiceSession } from "@openvox/sdk";
 *   const sess = new VoiceSession({
 *     baseWsUrl: "ws://localhost:3001",
 *     options: { agentId: "abc..." },
 *   });
 *   sess.on(ev => console.log(ev));
 *   await sess.start();
 *   sess.sendPcm(new Int16Array(...));
 *
 * In the browser, use VoiceSession.fromMicrophone() which also handles
 * `getUserMedia` and PCM resampling.
 */

import type { VoiceEvent, VoiceSessionOptions } from "./types.js";

type Listener = (ev: VoiceEvent) => void;

export class VoiceSession {
  private ws: WebSocket | null = null;
  private listeners: Listener[] = [];

  constructor(
    private readonly cfg: { baseWsUrl: string; options?: VoiceSessionOptions; path?: string },
  ) {}

  on(fn: Listener) {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((f) => f !== fn);
    };
  }

  private emit(ev: VoiceEvent) {
    for (const fn of this.listeners) fn(ev);
  }

  async start(): Promise<void> {
    const url = `${this.cfg.baseWsUrl.replace(/\/$/, "")}${this.cfg.path ?? "/ws/voice"}`;
    const WSImpl = typeof WebSocket !== "undefined" ? WebSocket : ((await import("ws")).default as unknown as typeof WebSocket);
    const ws = new WSImpl(url);
    (ws as any).binaryType = "arraybuffer";
    this.ws = ws;
    await new Promise<void>((res, rej) => {
      ws.onopen = () => res();
      ws.onerror = () => rej(new Error("ws error"));
    });
    const o = this.cfg.options ?? {};
    ws.send(
      JSON.stringify({
        type: "start",
        agent_id: o.agentId,
        system_prompt: o.systemPrompt,
        llm_provider: o.llmProvider,
        llm_model: o.llmModel,
        stt_provider: o.sttProvider,
        tts_provider: o.ttsProvider,
        voice_id: o.voiceId,
        voice_language: o.voiceLanguage,
        sample_rate: o.sampleRate ?? 16000,
      }),
    );

    ws.onmessage = (e: any) => {
      if (typeof e.data === "string") {
        const obj = JSON.parse(e.data);
        this.emit(obj as VoiceEvent);
      } else {
        this.emit({
          type: "audio",
          chunk: e.data as ArrayBuffer,
          sampleRate: 24000,
          encoding: "pcm16",
        });
      }
    };
    ws.onclose = () => {
      this.ws = null;
    };
  }

  sendPcm(pcm: Int16Array | ArrayBuffer): void {
    if (!this.ws) throw new Error("not started");
    const data = pcm instanceof Int16Array ? pcm.buffer : pcm;
    this.ws.send(data as any);
  }

  end(): void {
    if (!this.ws) return;
    this.ws.send(JSON.stringify({ type: "end" }));
  }

  interrupt(): void {
    if (!this.ws) return;
    this.ws.send(JSON.stringify({ type: "interrupt" }));
  }

  close(): void {
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* ignore */
      }
      this.ws = null;
    }
  }
}
