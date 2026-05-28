/**
 * Browser-side audio helpers for the playground.
 *
 *   - capture: getUserMedia → PCM s16le @16k for STT.
 *   - playback: queue assistant PCM s16le frames (any sample rate) and
 *     play them through a single AudioContext, gap-free.
 */

export type CaptureHandle = {
  stream: MediaStream;
  stop: () => void;
};

export async function captureMicrophone(
  onPcm: (frame: Int16Array) => void,
  opts: { sampleRate?: number } = {},
): Promise<CaptureHandle> {
  const targetRate = opts.sampleRate ?? 16000;
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new AudioContext({ sampleRate: targetRate });
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  source.connect(proc);
  proc.connect(ctx.destination);

  proc.onaudioprocess = (e) => {
    const f32 = e.inputBuffer.getChannelData(0);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    onPcm(i16);
  };

  return {
    stream,
    stop: () => {
      proc.disconnect();
      source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
    },
  };
}

// ── Playback queue ────────────────────────────────────────────────
//
// Streaming TTS arrives as many small PCM chunks. We schedule each as an
// AudioBufferSource starting at the rolling `cursor`. To avoid clicks on
// the very first chunk (when `currentTime` may already be a few ms ahead
// of the initial cursor) we kick the cursor a small look-ahead into the
// future. `resume()` is called in case the AudioContext starts in
// `suspended` (Chrome's autoplay policy can do that even after a click).
const LOOKAHEAD_S = 0.06;

export class AudioPlaybackQueue {
  private ctx: AudioContext;
  private cursor: number;
  private active: AudioBufferSourceNode[] = [];

  constructor() {
    this.ctx = new AudioContext();
    this.cursor = this.ctx.currentTime + LOOKAHEAD_S;
  }

  enqueuePcm16(pcm: ArrayBuffer, sampleRate = 24000) {
    if (pcm.byteLength < 2) return;
    const usable = pcm.byteLength - (pcm.byteLength % 2);
    const i16 = new Int16Array(pcm, 0, usable / 2);
    if (i16.length === 0) return;

    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const buf = this.ctx.createBuffer(1, f32.length, sampleRate);
    buf.copyToChannel(f32, 0);

    if (this.ctx.state === "suspended") {
      this.ctx.resume().catch(() => {});
    }

    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    // If the queue has fallen behind real-time, reset to a small look-ahead
    // so we never start in the past (which would be silently truncated).
    const startAt = Math.max(this.cursor, this.ctx.currentTime + LOOKAHEAD_S);
    src.start(startAt);
    this.cursor = startAt + buf.duration;
    this.active.push(src);
    src.onended = () => {
      this.active = this.active.filter((s) => s !== src);
    };
  }

  enqueueMp3(_data: ArrayBuffer) {
    // For MP3 / Opus we'd decode via decodeAudioData; left as a stub —
    // BytePlus default returns PCM16 which we handle above.
  }

  stopAll() {
    this.active.forEach((s) => {
      try {
        s.stop();
      } catch {
        // ignore
      }
    });
    this.active = [];
    this.cursor = this.ctx.currentTime + LOOKAHEAD_S;
  }

  /**
   * Are any audio buffers currently playing OR pending playback?
   *
   * Used by the playground (and any other voice consumer) to decide
   * whether a fresh STT `user_partial` event represents a BARGE-IN
   * (user interrupted mid-response) vs. a NEW TURN (user starting
   * to speak when assistant was silent). Barge-in requires draining
   * the queue + notifying the server; new-turn doesn't.
   *
   * Implementation note: `active` tracks every AudioBufferSourceNode
   * we've started. The `onended` handler removes each entry on
   * natural completion. So `active.length > 0` is true iff at
   * least one node is scheduled and hasn't finished playing yet.
   */
  isPlaying(): boolean {
    return this.active.length > 0;
  }

  close() {
    this.stopAll();
    this.ctx.close().catch(() => {});
  }
}
