/**
 * BytePlus Seed ASR 2.0 — streaming WebSocket STT provider.
 *
 * Wire protocol (binary WebSocket frames):
 *
 *   ┌───────────────────────┐
 *   │ header     (4 bytes)  │
 *   ├───────────────────────┤
 *   │ [sequence  (4 bytes)] │  ← only present when header byte1 lo bit 0 is set
 *   ├───────────────────────┤  ← THIS WAS THE BUG: we must consume it before reading payload size
 *   │ payload size (BE u32) │
 *   ├───────────────────────┤
 *   │ payload               │  ← may be gzip-compressed JSON
 *   └───────────────────────┘
 *
 * Header layout:
 *   byte 0 hi nibble: protocol version (0x1 = v1)
 *   byte 0 lo nibble: header size in 4-byte units (0x1 = 4 bytes, so header = 4 bytes total)
 *   byte 1 hi nibble: message type
 *                       0x1 = full client request (first frame, carries JSON config)
 *                       0x2 = audio-only request  (subsequent frames, raw PCM)
 *                       0x9 = full server response
 *                       0xF = error response
 *   byte 1 lo nibble: message-type-specific flags
 *                       bit 0: 1 → sequence number field IS present in this frame
 *                       bit 1: 1 → this is the last audio packet (client) or last response (server)
 *   byte 2 hi nibble: serialization  (0x0 = raw, 0x1 = JSON)
 *   byte 2 lo nibble: compression    (0x0 = none, 0x1 = gzip)
 *   byte 3:           reserved (always 0)
 */

import { createGunzip } from "node:zlib";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { gunzip as gunzipCb } from "node:zlib";
import WebSocket from "ws";

const gunzip = promisify(gunzipCb);

// ── endpoints ────────────────────────────────────────────────────────────────

const WS_URL =
  "wss://voice.ap-southeast-1.bytepluses.com/api/v3/sauc/bigmodel_async";
const FILE_SUBMIT_URL =
  "https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/submit";
const FILE_QUERY_URL =
  "https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/query";

const RESOURCE_ID_STREAM = "volc.seedasr.sauc.duration";
const RESOURCE_ID_FILE = "volc.seedasr.auc";

// ── types ────────────────────────────────────────────────────────────────────

export type AudioChunk = {
  data: Buffer;
  isFinal?: boolean;
};

export type STTConfig = {
  sampleRate: number;
  language?: string;
};

export type STTResult = {
  text: string;
  isFinal: boolean;
  confidence: number;
  language: string;
  raw?: unknown;
};

type ParsedFrame =
  | { kind: "error"; code: number; message: string }
  | { kind: "response"; payload: Record<string, unknown> }
  | { kind: "empty" };

// ── frame builders (client → server) ─────────────────────────────────────────

/**
 * First frame: full client request carrying the JSON config blob.
 * header[0] = 0x11 → version=1, headerSize=1 (=4 bytes)
 * header[1] = 0x10 → msgType=0x1 (full request), flags=0x0 (no sequence, not last)
 * header[2] = 0x10 → serialization=JSON(0x1), compression=none(0x0)
 * header[3] = 0x00 → reserved
 */
function frameFullRequest(payload: Buffer): Buffer {
  const header = Buffer.from([0x11, 0x10, 0x10, 0x00]);
  const size = Buffer.allocUnsafe(4);
  size.writeUInt32BE(payload.length, 0);
  return Buffer.concat([header, size, payload]);
}

/**
 * Subsequent frames: audio-only carrying raw PCM bytes.
 * header[1] = (0x2 << 4) | flags
 *   flags bit 1 = 1 when this is the last packet so server knows to flush.
 *   We never send a sequence number from the client (bit 0 stays 0).
 */
function frameAudio(audio: Buffer, isLast = false): Buffer {
  const flags = isLast ? 0x02 : 0x00;
  const header = Buffer.from([0x11, (0x02 << 4) | flags, 0x00, 0x00]);
  const size = Buffer.allocUnsafe(4);
  size.writeUInt32BE(audio.length, 0);
  return Buffer.concat([header, size, audio]);
}

// ── frame parser (server → client) ───────────────────────────────────────────

/**
 * Parse a binary server frame into a typed result.
 *
 * The critical bug we already hit in Python and must not repeat:
 *   When header byte1 lo bit 0 is set (which is ALWAYS the case with the
 *   default server config), there are 4 extra bytes for the sequence number
 *   between the header and the payload-size field.
 *   If you skip this and read payload-size immediately after the 4-byte header,
 *   you read the sequence bytes as the size → massive garbage payload.
 */
async function parseFrame(data: Buffer): Promise<ParsedFrame> {
  if (data.length < 4) return { kind: "empty" };

  // byte 0: version (hi) + headerSizeUnits (lo)
  const headerSizeUnits = data[0] & 0x0f;
  const headerSize = headerSizeUnits * 4; // in bytes — currently always 4

  // byte 1: msgType (hi) + flags (lo)
  const msgType = (data[1] >> 4) & 0x0f;
  const flags = data[1] & 0x0f;

  // byte 2: serialization (hi) + compression (lo)
  const serialization = (data[2] >> 4) & 0x0f;
  const compression = data[2] & 0x0f;

  // Error frame: 0xF
  // Layout after header: error_code(4) + error_msg_size(4) + utf8 message
  if (msgType === 0x0f) {
    if (data.length < headerSize + 8) return { kind: "empty" };
    const code = data.readUInt32BE(headerSize);
    const msgSize = data.readUInt32BE(headerSize + 4);
    const message = data
      .subarray(headerSize + 8, headerSize + 8 + msgSize)
      .toString("utf8");
    return { kind: "error", code, message };
  }

  // Normal server response frame (msgType = 0x9)
  // THE FIX: check flags bit 0 before reading payload size.
  // If set, the server included a 4-byte sequence number right after the header.
  // We don't use the sequence value but we MUST skip past it.
  let offset = headerSize;
  if (flags & 0x01) {
    offset += 4; // skip sequence number — this was the bug
  }

  if (data.length < offset + 4) return { kind: "empty" };
  const payloadSize = data.readUInt32BE(offset);
  offset += 4;

  if (data.length < offset + payloadSize) return { kind: "empty" };
  let payload = data.subarray(offset, offset + payloadSize);

  // Decompress if needed
  if (compression === 0x01) {
    try {
      payload = await gunzip(payload) as Buffer;
    } catch {
      return { kind: "empty" };
    }
  }

  // Deserialize JSON
  if (serialization === 0x01) {
    try {
      const parsed = JSON.parse(payload.toString("utf8")) as Record<string, unknown>;
      return { kind: "response", payload: parsed };
    } catch {
      return { kind: "empty" };
    }
  }

  return { kind: "empty" };
}

// ── result extractor ─────────────────────────────────────────────────────────

/**
 * Pull (text, isFinal, confidence, language) out of a server response payload.
 *
 * The BytePlus response wraps everything inside payload_msg.result — not at
 * the top level. This was another source of confusion early on.
 *
 * isFinal logic:
 *   In bigmodel_async dual-pass mode, utterances with definite:true have been
 *   re-recognised by the non-streaming pass and are the accurate finals.
 *   is_last_package:true is the fallback when no definite utterances exist.
 */
function extractResult(obj: Record<string, unknown>): {
  text: string;
  isFinal: boolean;
  confidence: number;
  language: string;
  utterances: unknown[];
} {
  const payloadMsg = (obj.payload_msg ?? {}) as Record<string, unknown>;
  const res = (payloadMsg.result ?? obj.result ?? {}) as Record<string, unknown>;

  const text = (res.text as string | undefined) ?? "";
  const utterances = (res.utterances as unknown[] | undefined) ?? [];
  const additions = (res.additions as Record<string, unknown> | undefined) ?? {};
  const confidence = parseFloat(String(additions.confidence ?? "0")) || 0;
  const language =
    (res.language as string | undefined) ??
    (additions.language as string | undefined) ??
    "";

  // definite:true utterances are the reliable finals from the second pass
  const hasDefinite = utterances.some(
    (u) => (u as Record<string, unknown>).definite === true,
  );
  const isLastPackage = obj.is_last_package === true;
  const isFinal = hasDefinite || isLastPackage;

  return { text, isFinal, confidence, language, utterances };
}

// ── provider ─────────────────────────────────────────────────────────────────

export class BytePlusSTT {
  private readonly apiKey: string;

  constructor(apiKey: string) {
    this.apiKey = apiKey;
  }

  isAvailable(): boolean {
    return Boolean(this.apiKey);
  }

  /**
   * Stream audio chunks to BytePlus ASR 2.0 and yield STTResults.
   *
   * Two concurrent concerns run here:
   *   1. pumpAudio(): consumes the audio async-iterable and sends frames over WS
   *   2. receive loop: reads server frames and yields STTResult events
   *
   * They share the WebSocket but don't need to coordinate — WebSocket is
   * full-duplex so sends and receives are independent.
   *
   * We track seenDefiniteText to avoid emitting the same finalised text twice.
   * The server often re-includes already-promoted definite text in subsequent
   * partial frames.
   */
  async *transcribeStream(
    audio: AsyncIterable<AudioChunk>,
    config: STTConfig,
  ): AsyncGenerator<STTResult> {
    if (!this.isAvailable()) {
      throw new Error("BYTEPLUS_VOICE_API_KEY is not set");
    }

    const ws = new WebSocket(WS_URL, {
      headers: {
        "X-Api-Key": this.apiKey,
        "X-Api-Resource-Id": RESOURCE_ID_STREAM,
        "X-Api-Connect-Id": randomUUID(),
      },
      maxPayload: 16 * 1024 * 1024,
      // rejectUnauthorized: false  ← set via OPENVOX_INSECURE_TLS equivalent
    });

    // Wait for the connection to open before sending the config frame
    await new Promise<void>((resolve, reject) => {
      ws.once("open", resolve);
      ws.once("error", reject);
    });

    // Initial JSON config frame
    const startConfig = {
      user: { uid: randomUUID() },
      audio: {
        format: "pcm",
        codec: "raw",
        rate: config.sampleRate,
        bits: 16,
        channel: 1,
      },
      request: {
        model_name: "bigmodel",
        enable_itn: true,
        enable_punc: true,
        enable_ddc: true,
        show_utterances: true,
        enable_nonstream: true, // dual-pass: streaming partials + accurate finals
        result_type: "full",
        end_window_size: 800,
      },
    };
    ws.send(frameFullRequest(Buffer.from(JSON.stringify(startConfig), "utf8")));

    // Collect incoming binary frames into a queue for the async generator to consume
    const frameQueue: Array<Buffer | "done" | Error> = [];
    let resolveWaiting: (() => void) | null = null;

    const enqueue = (item: Buffer | "done" | Error) => {
      frameQueue.push(item);
      resolveWaiting?.();
      resolveWaiting = null;
    };

    ws.on("message", (data, isBinary) => {
      // The ws library always delivers binary data as Buffer.
      // Discriminate on isBinary, not instanceof Buffer (always true).
      if (isBinary) {
        enqueue(data as Buffer);
      } else {
        // Some intermediaries downgrade to text — treat as JSON response
        try {
          const parsed = JSON.parse((data as Buffer).toString("utf8")) as Record<string, unknown>;
          enqueue(Buffer.from(JSON.stringify(parsed)));
        } catch {
          // ignore malformed text frames
        }
      }
    });

    // Server closes cleanly with code 1000 after last frame — that's normal, not an error
    ws.on("close", (code) => {
      if (code === 1000 || code === 1001) {
        enqueue("done");
      } else {
        enqueue(new Error(`WebSocket closed unexpectedly: code=${code}`));
      }
    });

    ws.on("error", (err) => enqueue(err));

    // Pump audio in the background — fire and forget, errors surface via WS close
    let sendDone = false;
    const pumpAudio = async () => {
      let pending: Buffer | null = null;
      for await (const chunk of audio) {
        if (!chunk.data || chunk.data.length === 0) continue;
        if (pending !== null) {
          ws.send(frameAudio(pending, false));
        }
        pending = chunk.data;
        if (chunk.isFinal) {
          ws.send(frameAudio(pending, true));
          pending = null;
        }
      }
      if (pending !== null) {
        ws.send(frameAudio(pending, true));
      }
      sendDone = true;
    };

    // Don't await — run concurrently with the receive loop below
    const pump = pumpAudio().catch((err) => {
      enqueue(err instanceof Error ? err : new Error(String(err)));
    });

    // Receive loop
    let seenDefiniteText = "";
    try {
      while (true) {
        // Wait for the next frame (with 30s timeout matching Python impl)
        const item = await Promise.race([
          new Promise<Buffer | "done" | Error>((resolve) => {
            if (frameQueue.length > 0) {
              resolve(frameQueue.shift()!);
            } else {
              resolveWaiting = () => resolve(frameQueue.shift()!);
            }
          }),
          new Promise<"timeout">((resolve) =>
            setTimeout(() => resolve("timeout"), 30_000),
          ),
        ]);

        if (item === "timeout") {
          if (sendDone) return; // no more audio, server just hasn't responded — give up
          continue;
        }

        if (item === "done") return;

        if (item instanceof Error) {
          throw item;
        }

        const frame = await parseFrame(item);

        if (frame.kind === "empty") continue;

        if (frame.kind === "error") {
          yield {
            text: "",
            isFinal: true,
            confidence: 0,
            language: config.language ?? "",
            raw: { error: frame.message, code: frame.code },
          };
          return;
        }

        const obj = frame.payload;
        const { text, isFinal, confidence, language } = extractResult(obj);

        if (!text) {
          // Empty heartbeat / ack — check if it's also the last package
          if (obj.is_last_package === true && sendDone) return;
          continue;
        }

        // Deduplicate: definite finals are often re-included in subsequent partial frames
        if (isFinal) {
          if (text === seenDefiniteText) {
            if (obj.is_last_package === true && sendDone) return;
            continue;
          }
          seenDefiniteText = text;
        }

        yield {
          text,
          isFinal,
          confidence,
          language: language || config.language || "",
          raw: obj,
        };

        if (isFinal && obj.is_last_package === true && sendDone) return;
      }
    } finally {
      // Cancel the pump task and close the WebSocket cleanly
      await pump;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    }
  }

  /**
   * Submit an audio file URL for async batch transcription and poll for the result.
   * Audio URL must be publicly reachable (or presigned BytePlus TOS URL).
   */
  async transcribeFileUrl(
    audioUrl: string,
    opts: {
      language?: string;
      format?: string;
      channel?: number;
      enableSpeakerInfo?: boolean;
      enablePunc?: boolean;
      enableItn?: boolean;
      showUtterances?: boolean;
      timeoutMs?: number;
      pollIntervalMs?: number;
    } = {},
  ): Promise<Record<string, unknown>> {
    if (!this.isAvailable()) {
      throw new Error("BYTEPLUS_VOICE_API_KEY is not set");
    }

    const {
      language,
      format = "mp3",
      channel = 1,
      enableSpeakerInfo = false,
      enablePunc = true,
      enableItn = true,
      showUtterances = true,
      timeoutMs = 300_000,
      pollIntervalMs = 2_000,
    } = opts;

    const requestId = randomUUID();
    const headers: Record<string, string> = {
      "X-Api-Key": this.apiKey,
      "X-Api-Resource-Id": RESOURCE_ID_FILE,
      "X-Api-Request-Id": requestId,
      "X-Api-Sequence": "-1",
      "Content-Type": "application/json",
    };

    const body: Record<string, unknown> = {
      user: { uid: randomUUID() },
      audio: { url: audioUrl, format, channel, ...(language ? { language } : {}) },
      request: {
        model_name: "bigmodel",
        enable_punc: enablePunc,
        enable_itn: enableItn,
        enable_speaker_info: enableSpeakerInfo,
        show_utterances: showUtterances,
      },
    };

    const submit = await fetch(FILE_SUBMIT_URL, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const submitStatus = submit.headers.get("X-Api-Status-Code") ?? "";
    if (submit.status !== 200 || submitStatus !== "20000000") {
      throw new Error(
        `submit failed: status=${submit.status} x-api=${submitStatus} msg=${submit.headers.get("X-Api-Message")}`,
      );
    }

    const deadline = Date.now() + timeoutMs;
    while (true) {
      if (Date.now() > deadline) {
        throw new Error("auc transcription timed out");
      }
      await new Promise((r) => setTimeout(r, pollIntervalMs));

      const q = await fetch(FILE_QUERY_URL, {
        method: "POST",
        headers,
        body: JSON.stringify({}),
      });
      const code = q.headers.get("X-Api-Status-Code") ?? "";
      if (code === "20000000") {
        return (await q.json()) as Record<string, unknown>;
      }
      if (code === "20000001" || code === "20000002") {
        continue; // still processing or queued
      }
      throw new Error(
        `query failed: status=${q.status} x-api=${code} msg=${q.headers.get("X-Api-Message")}`,
      );
    }
  }
}
