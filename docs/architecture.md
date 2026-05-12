# OpenVox architecture

> **Looking for the diagrams?** See [`diagrams.md`](diagrams.md) — eight
> Mermaid diagrams covering system tiers, the voice pipeline sequence,
> module layout, extensibility surface (skills/templates/scheduler/MCP),
> data model, provider plug-ins, request paths, and deployment topology.
> This file is the prose explanation of *why* the design works.

## The pipeline

A voice agent has to do four things in a tight loop:

1. **Capture** the user's voice (microphone, phone, WhatsApp).
2. **Transcribe** the audio (STT).
3. **Reason** over the transcript (LLM, optionally calling skills).
4. **Speak back** (TTS).

Latency is the entire ballgame. The wall-clock time between the user finishing
their sentence and hearing the agent's first word should sit under ~300 ms,
otherwise the conversation feels off. OpenVox hits that budget by:

- Running each stage as an **async generator** that pushes its output to the
  next stage as soon as it has *anything* to push.
- Cutting the LLM token stream into **sentence-sized chunks** for TTS — we
  don't wait for the full response before audio starts playing.
- Letting the user **interrupt** at any point: when the STT provider says
  "user is speaking again", we cancel the in-flight TTS stream.

The orchestrator that wires this together lives in
`packages/core/openvox/pipeline/orchestrator.py`. Read it from top to bottom —
the comments explain each decision.

## Components

```
                          ┌──────────────────┐
                          │     Dashboard    │ Next.js 14, React, Tailwind
                          └────────┬─────────┘
                                   │ HTTP + WS
                          ┌────────▼─────────┐
                          │   Node Gateway   │ Fastify · auth · rate-limit
                          └────────┬─────────┘
                                   │ HTTP proxy + WS bridge
                          ┌────────▼─────────┐
   inbound calls ────────►│   Python Core    │ FastAPI · asyncio · pipeline
   (Twilio, WhatsApp,     └────────┬─────────┘
    Telegram)                      │
                          ┌────────┴─────────┐
                          │                  │
                  ┌───────▼───┐      ┌───────▼───────┐
                  │ Providers │      │ Skills runner │
                  └───────────┘      └───────────────┘
                  STT │ TTS │ LLM │ RTC
```

### Core (Python)

- **FastAPI** for REST.
- **`websockets`** for the realtime audio path (`/ws/voice`).
- **SQLAlchemy 2.x** async over Postgres (or SQLite for local-first).
- **Provider registry** with first-class entry-point discovery.
- **Skill registry** that loads built-ins, entry-points, and a local
  `~/.openvox/skills/` directory on startup.

### Server (Node.js)

A thin Fastify gateway. It exists for three reasons:

1. To layer JWT/OAuth and rate-limit at one common seam (so the Python core
   doesn't have to re-implement Node-style auth).
2. To bridge the browser WebSocket to the Python core's WS — letting us add
   per-connection middleware later.
3. To handle telephony webhooks and convert them to internal events.

It is, by design, *thin*. Business logic lives in core.

### Dashboard (Next.js)

App-router Next.js 14, all client components (`"use client"`). Talks to the
Node gateway only. The playground page is the most interesting one — it
captures the mic with `getUserMedia`, downsamples to 16 kHz s16le PCM, streams
frames to the WS pipeline, and queues incoming audio frames into a single
`AudioContext` for gap-free playback.

### SDKs

`@openvox/sdk` (TypeScript) and `openvox-sdk` (Python) are thin client
wrappers around the gateway. They share types and a `VoiceSession` class so
you can drive the live pipeline from your own app.

### CLI

`openvox` (Node-based) for shell-script-friendly automation:

```bash
openvox status
openvox templates list
openvox agents create --template education-tutor --name "Algebra coach"
openvox skills new my_skill
```

## Extension model

Inspired by OpenClaw's plugin design and refined for voice:

- **Skills** are class-based (`BaseSkill`) or decorator-style (`@skill`).
  They expose JSON-schema parameters and run async.
- **Providers** subclass `STTProvider`/`TTSProvider`/`LLMProvider`/`RTCProvider`
  and either register at startup (built-ins) or via the `openvox.providers`
  entry-point group (third-party packages).
- **Templates** are static catalogue entries that bundle a system-prompt + skills.
- **Channels** plug into the orchestrator at the audio-frame level.

## Latency budget

For a typical turn (16 kHz, BytePlus Seed):

```
mic capture buffer        ~80 ms   (2048 samples @ 16k = 128 ms; we flush mid-buffer)
STT first-final           ~80 ms   (Seed ASR streaming)
LLM first-token           ~120 ms  (Doubao Seed-2.0)
TTS first-audio chunk     ~60 ms   (Seed Speech 2.0)
audio playback start      ~10 ms
                          ──────
                          ~250 ms first-audio after the user stops talking
```

That's the green path. Tool calls, fall-back providers, or full-duplex
(speech-to-speech) push past the budget — design accordingly.

## Privacy & GDPR

Out of the box:

- `ENABLE_AUDIO_STORAGE=false` — we keep transcripts but not raw audio.
- `PII_MASKING_ENABLED=true` — credit-card / phone / email patterns scrubbed.
- `DATA_RETENTION_DAYS=30` — sessions are pruned after this window.
- `DATA_RESIDENCY_REGION` — pin where storage uploads land.
- Local-first defaults mean nothing leaves your machine until you opt in.
