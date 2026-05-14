/**
 * Thin WebSocket client speaking OpenVox's `/ws/voice` protocol.
 *
 * Wire format (mirrors `packages/core/openvox/api/ws/voice.py`):
 *   client → server (text)   {"type":"start","agent_id":"...","sample_rate":16000,...}
 *   client → server (binary) PCM s16le mono frames @ 16 kHz
 *   client → server (text)   {"type":"end" | "interrupt"}
 *
 *   server → client (text)   {"type":"user_partial|user_final|assistant_token|
 *                                       assistant_done|skill_call|skill_result|
 *                                       tts_error|error|interrupt", ...}
 *   server → client (binary) PCM s16le frames (sample_rate per message header)
 *
 * The SDK intentionally re-emits the server frames verbatim through the
 * `onEvent` callback. Apps can render their own transcripts, debug
 * panes, or skill timelines without us prescribing UI choices.
 */

export type VoiceEvent =
  | { type: "user_partial" | "user_final"; text: string; data?: any }
  | { type: "assistant_token" | "assistant_done"; text: string }
  | { type: "skill_call" | "skill_result"; text: string; args?: any; output?: any }
  | { type: "interrupt" }
  | { type: "tts_error" | "error"; text?: string; data?: any }
  | { type: "audio"; pcm: ArrayBuffer; sampleRate: number };

export interface StartArgs {
  /** WS URL — usually `${wsBase}/ws/voice` where wsBase is your gateway. */
  url: string;
  /** Agent UUID to bind the session to. */
  agentId: string;
  /** Optional STT sample-rate override (default 16000). */
  sampleRate?: number;
}

export class VoiceWS {
  private ws: WebSocket | null = null;
  private lastAudioMeta: { sampleRate: number } | null = null;

  async connect(args: StartArgs, onEvent: (ev: VoiceEvent) => void): Promise<void> {
    this.ws = new WebSocket(args.url);
    this.ws.binaryType = "arraybuffer";

    await new Promise<void>((resolve, reject) => {
      if (!this.ws) return reject(new Error("ws unset"));
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => reject(e);
    });

    this.ws.onmessage = (msg) => {
      if (typeof msg.data === "string") {
        try {
          const parsed = JSON.parse(msg.data);
          // The server sends `sample_rate` alongside assistant_audio
          // metadata; binary frames that follow carry the actual PCM.
          if (parsed.type === "assistant_audio") {
            this.lastAudioMeta = { sampleRate: parsed.sample_rate ?? 24000 };
            return;
          }
          onEvent(parsed as VoiceEvent);
        } catch {
          // Ignore non-JSON text frames.
        }
      } else if (msg.data instanceof ArrayBuffer) {
        // Audio: the immediately-preceding text frame told us the rate.
        onEvent({
          type: "audio",
          pcm: msg.data,
          sampleRate: this.lastAudioMeta?.sampleRate ?? 24000,
        });
      }
    };

    this.ws.send(JSON.stringify({
      type: "start",
      agent_id: args.agentId,
      sample_rate: args.sampleRate ?? 16000,
    }));
  }

  pushAudio(pcm: Uint8Array): void {
    // Note: many browsers wrap typed arrays such that .buffer has a larger
    // capacity than the array's view — slice to the exact range so we
    // don't ship trailing garbage over the WS.
    this.ws?.send(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength));
  }

  interrupt(): void {
    this.ws?.send(JSON.stringify({ type: "interrupt" }));
  }

  end(): void {
    try {
      this.ws?.send(JSON.stringify({ type: "end" }));
    } catch { /* socket may already be closing */ }
    this.ws?.close();
    this.ws = null;
  }
}
