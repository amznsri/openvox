/**
 * Browser audio plumbing for the OpenVox Web SDK.
 *
 * Two halves:
 *   - `MicCapture` runs a ScriptProcessor (works in every modern
 *     browser without AudioWorklet boilerplate). It downsamples whatever
 *     the user's mic gives us (usually 44.1 or 48 kHz) into 16 kHz PCM
 *     s16le and yields Uint8Array chunks.
 *   - `PcmPlayer` queues PCM s16le chunks from the server and plays them
 *     back through a single AudioContext. Schedule-ahead is critical:
 *     without a small lookahead the first chunk lands "in the past"
 *     and Chrome pops loudly.
 *
 * Notes:
 *   - We deliberately don't use AudioWorklet here. The worklet path
 *     needs a separate JS module file you publish alongside your bundle,
 *     which kills the "1 npm install and you're done" pitch. ScriptProcessor
 *     is deprecated but still works in every browser shipping in 2026.
 *   - Safari is fussy about AudioContext.resume() — we call it on every
 *     start() because user-gesture context can be lost between sessions.
 */

const TARGET_RATE = 16000;

/** Downsample a Float32Array from `srcRate` to `dstRate` and convert to PCM s16le. */
export function downsampleToS16(input: Float32Array, srcRate: number, dstRate: number = TARGET_RATE): Uint8Array {
  if (dstRate === srcRate) {
    return float32ToS16(input);
  }
  const ratio = srcRate / dstRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  let offset = 0;
  for (let i = 0; i < outLen; i++) {
    const idx = Math.floor(offset);
    out[i] = input[idx] ?? 0;
    offset += ratio;
  }
  return float32ToS16(out);
}

function float32ToS16(input: Float32Array): Uint8Array {
  const out = new ArrayBuffer(input.length * 2);
  const view = new DataView(out);
  for (let i = 0; i < input.length; i++) {
    // Hard-clip rather than saturating to keep loudness consistent.
    const s = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(out);
}

// ── Mic capture ──────────────────────────────────────────────────────

export type MicChunkHandler = (pcm16: Uint8Array) => void;

export class MicCapture {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;

  async start(onChunk: MicChunkHandler): Promise<void> {
    if (this.processor) return; // already running
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    this.ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    await this.ctx.resume();
    const srcRate = this.ctx.sampleRate;
    this.source = this.ctx.createMediaStreamSource(this.stream);
    // bufferSize 4096 → ~85 ms of audio per chunk @ 48 kHz. Smaller
    // buffers are lower-latency but cost more JS event-loop traffic.
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      onChunk(downsampleToS16(input, srcRate, TARGET_RATE));
    };
    this.source.connect(this.processor);
    // Connect to a muted gain so the worklet actually pumps — connecting
    // straight to destination would echo the user's voice back to them.
    const muted = this.ctx.createGain();
    muted.gain.value = 0;
    this.processor.connect(muted);
    muted.connect(this.ctx.destination);
  }

  async stop(): Promise<void> {
    try { this.processor?.disconnect(); } catch { /* ignore */ }
    try { this.source?.disconnect(); } catch { /* ignore */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    try { await this.ctx?.close(); } catch { /* ignore */ }
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.ctx = null;
  }
}

// ── PCM playback ─────────────────────────────────────────────────────

const LOOKAHEAD_SEC = 0.06; // schedule 60 ms ahead so first chunk has room to materialise

export class PcmPlayer {
  private ctx: AudioContext | null = null;
  private nextStart = 0;
  private buffered: AudioBufferSourceNode[] = [];

  /** Lazily create / resume the context. Must be called from a user gesture (button click etc.). */
  async ensureContext(): Promise<void> {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || (window as any).webkitAudioContext)({ sampleRate: 24000 });
    }
    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }
  }

  /** Enqueue a PCM s16le chunk at `sampleRate` for back-to-back playback. */
  enqueue(pcm: ArrayBuffer, sampleRate: number): void {
    if (!this.ctx) return;
    // Trim odd byte tail — Int16Array constructor throws otherwise.
    const bytes = pcm.byteLength % 2 === 0 ? pcm : pcm.slice(0, pcm.byteLength - 1);
    if (bytes.byteLength === 0) return;
    const s16 = new Int16Array(bytes);
    const buf = this.ctx.createBuffer(1, s16.length, sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < s16.length; i++) {
      ch[i] = s16[i] / 0x8000;
    }
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    const now = this.ctx.currentTime;
    const startAt = Math.max(now + LOOKAHEAD_SEC, this.nextStart);
    src.start(startAt);
    this.nextStart = startAt + buf.duration;
    this.buffered.push(src);
    src.onended = () => {
      const i = this.buffered.indexOf(src);
      if (i >= 0) this.buffered.splice(i, 1);
    };
  }

  /** Stop everything currently queued — used on barge-in. */
  clear(): void {
    for (const src of this.buffered) {
      try { src.stop(); } catch { /* already stopped */ }
    }
    this.buffered = [];
    this.nextStart = this.ctx?.currentTime ?? 0;
  }

  async close(): Promise<void> {
    this.clear();
    try { await this.ctx?.close(); } catch { /* ignore */ }
    this.ctx = null;
  }
}
