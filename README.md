<div align="center">

# OpenVox

**The open-source platform for building production voice agents.**

Web, phone, WhatsApp, Telegram. 41 voices, 7 languages out of the box. Pluggable providers — bring
your own LLM, STT, and TTS — or skip the pipeline entirely with OpenAI Realtime (S2S) for
single-WS sub-150 ms voice. Self-host on a laptop in 60 seconds. Apache-2.0.

[Quick install](#quick-install) · [Templates](#33-production-quality-templates) · [Provider matrix](#provider-matrix) · [Architecture](#under-the-hood)

</div>

---

## Why OpenVox?

The voice agent landscape today splits between **closed SaaS** (Vapi, Retell, Bland — fast to
start, expensive at scale, no control over your data) and **low-level OSS frameworks** (LiveKit
Agents, Pipecat — flexible but you wire up everything from skills to dashboards yourself).

OpenVox sits between them: an open-source platform that ships **every layer of the stack** — voice
pipeline, dashboard, SDK, CLI, runnable templates, observability, scheduling — so a non-technical
operator can build a working agent in five minutes, and a developer can drop into Python or
TypeScript to extend any piece.

- 🌍 **Multilingual out of the box** — 41 voices, 7 first-class languages (English, Mandarin,
  Cantonese, Spanish, Bahasa, French, Hindi) ship configured, with a multilingual-support
  template that covers 51+ languages via runtime language detection.
- 🎙️ **Build by voice** — describe the agent you want; the built-in setup assistant picks a
  template, names it, configures the voice, and walks you through publishing. Or use the
  dashboard. Or the SDK.
- ⚡ **Two voice modes per agent** — choose **Pipeline** (STT → LLM → TTS, ~300 ms first-byte,
  full provider mix-and-match) or **S2S** via OpenAI Realtime (single WS, ~120 ms first-byte,
  native function-calling). Toggle per agent.
- 🔌 **Provider-agnostic** — Anthropic · AssemblyAI · BytePlus · Cartesia · Deepgram · DeepSeek
  · ElevenLabs · Gemini · OpenAI · Whisper. Pick per agent, mix per layer.
- 🏠 **Self-hosted, no telemetry** — SQLite + filesystem out of the box. Audio + text travel
  only to providers you configure. Run fully on-device with local Whisper + Ollama + Piper.
- 🎯 **Sub-300 ms** end-to-end voice latency through sentence-level token streaming.
- 📞 **Multi-channel from day one** — same agent over web RTC, phone (Twilio), WhatsApp
  Business, Telegram. Channels share one pipeline; one agent reaches every surface.
- 🧰 **Extensible** — drop a Python skill in `~/.openvox/skills/`, register a custom provider,
  or wire any third-party tool via Model Context Protocol (MCP).
- 🧪 **Live playground + observability + evals** — talk to your agent in the browser, replay
  any session, score regressions.
- ⏰ **Built-in scheduler** — cron, interval, one-off, or webhook triggers. No external job
  runner. Simple-mode UI for non-technical scheduling.
- 🛡️ **GDPR-aware** — configurable retention, data residency, transcript-only mode, PII masking.

## Quick install

```bash
curl -fsSL https://github.com/amznsri/openvox/releases/latest/download/install.sh | bash
openvox start
```

That's it — ~45 seconds end-to-end. The installer detects Python 3.11+,
picks `pipx` (or a venv fallback), drops the `openvox` binary on `$PATH`,
then `openvox start` boots the daemon and serves the dashboard at
<http://localhost:8000/dashboard>. On first run, the setup wizard at
`/dashboard/setup` walks you through pasting your provider keys. No
compile step, no Docker required.

> Works on macOS + Linux. **Windows** users: pipx is supported
> (`pipx install openvox-core`); a self-contained `.exe` via WinGet is
> on the roadmap once a code-signing cert is in place.

### Alternative installers

Pick a different path if the one-liner doesn't match your setup —
they all install the same `openvox` binary:

```bash
# Already use pipx / pip? — works on macOS, Linux, Windows
pipx install openvox-core
pip install openvox-core      # ditto, but conflicts with PEP 668 systems

# Homebrew on macOS — slower (compiles from source) but more brew-native
brew install amznsri/openvox/openvox
```

> **Heads-up on Homebrew speed.** brew installs from sdists (compiles
> ~5 native deps via clang + rust), so expect 2-5 min vs the one-liner's
> ~45s. The end result is the same binary; only the install path
> differs.

### After install — start the daemon

```bash
openvox run              # foreground; Ctrl-C to stop. Auto-opens dashboard.
openvox start            # background daemon (launchd / systemd / Windows Service).
                         # Starts at login. Stop with `openvox stop`.
openvox status           # is it running?
openvox logs -f          # tail ~/.openvox/logs/openvox.log
```

`openvox info` shows resolved config (with secrets redacted) — the
fastest way to debug "is this thing configured?".

### Your first agent (~2 minutes)

1. **Add a provider key** — go to <http://localhost:8000/dashboard/settings/>,
   click any provider row (OpenAI, BytePlus, Anthropic, …), paste your key,
   Save. Keys are encrypted at rest in `~/.openvox/openvox.db` with a
   per-host key in `~/.openvox/secret.key`. Restart the daemon once
   (`openvox restart`) so the new key takes effect.
2. **Pick a template** — open <http://localhost:8000/dashboard/templates>
   and click *Copy template* on any of the 33 templates. Email Assistant
   and Calendar Scheduler are good if you connected a Google account on
   the Integrations tab; E-commerce Support and Multilingual Support
   work out of the box with just an LLM + voice key.
3. **Test it** — click *Test* on the new agent → Playground opens →
   **Tap to talk** and have a conversation. Say "Stop" mid-response to
   interrupt; the audio cuts within ~150 ms.

That's it. Edit the agent's system prompt, attach documents, add MCP
tools, or publish it to a channel (Twilio, WhatsApp, Telegram) from the
agent's Channels tab.

### Production install (Docker mode)

```bash
git clone https://github.com/amznsri/openvox.git
cd openvox
cp .env.example .env
$EDITOR .env       # add at least one LLM key + one voice key

docker compose up --build
open http://localhost:3000
```

Brings up four services: core + dashboard + postgres (+ optional
`--profile whatsapp` for WhatsApp Personal or `--profile tunnel`
for ngrok).

> **Upgrading an existing install?** If you're pulling the latest
> after a long pause and your old install used the v0.1.x Node
> gateway + Redis stack: those services were deleted in Phase 1.
> A one-time `docker stop openvox-server openvox-redis && docker rm
> openvox-server openvox-redis` clears the orphan containers
> before `docker compose up --build` brings up the new layout.

That's it. The dashboard ships with **33 ready-to-run templates** — pick one and you have a
working voice agent in under a minute. Or click **"Build by voice"** in the agent creator and
describe what you want.

## What ships in the box

### 33 production-quality templates

| Category          | Templates                                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Support**       | E-commerce support · Multilingual support (51+ languages) · Hotline (7 languages — EN / ZH / YUE / ES / ID / FR / HI) |
| **Sales**         | Outbound SDR (BANT + HubSpot MCP) · Telesales (7 languages) · Reactivation (7 languages)                           |
| **Productivity**  | Email assistant (Gmail) · Calendar scheduler (Google) · Executive assistant (Gmail + Calendar via one OAuth)       |
| **Front desk**    | Receptionist with in-app bookings                                                                                  |
| **Knowledge**     | Document Q&A with RAG + image-aware passage lookup                                                                 |
| **Education**     | Science & Math tutor with worked-example calculator                                                                |
| **Finance**       | Stock analyst with live quotes + technical indicators                                                              |
| **Analytics**     | Voice analyzer — sentiment + profanity + summarisation                                                             |
| **Meta**          | Setup assistant — build new agents conversationally by voice                                                       |

Click **Copy template** in the dashboard to instantiate any of them. Each copy is fully
editable — change the system prompt, swap the voice, attach documents, add skills, then
publish.

### Provider matrix

Provider-neutral by design. Mix and match per agent through the dashboard or `.env`:

| Layer       | Supported providers                                                                                    |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| **LLM**     | Anthropic · BytePlus · DeepSeek · Gemini · OpenAI · *(any OpenAI-compatible endpoint)*                |
| **STT**     | AssemblyAI · BytePlus · Deepgram · Whisper *(local or hosted)*                                         |
| **TTS**     | BytePlus · Cartesia · ElevenLabs · OpenAI TTS                                                          |
| **VAD**     | Silero *(local ONNX)* · BytePlus VAD                                                                   |
| **RTC**     | Browser WebSocket *(default)* · BytePlus RTC                                                           |
| **Phone**   | Twilio                                                                                                  |
| **Chat**    | WhatsApp Business · Telegram · WeChat Work · Lark                                                      |
| **Storage** | Local filesystem · AWS S3 · MinIO · BytePlus TOS · GCS · Alibaba OSS                                   |

> **First-run tip:** BytePlus gives you LLM + STT + TTS under a single API key, which is the
> lowest-friction way to try OpenVox. Swap any layer per agent once you're up.

**Speech-to-speech** (single-WS voice via OpenAI Realtime) ships today as an alternative to
the STT/LLM/TTS pipeline — toggle it per agent on the Voice tab. Live interpretation and
voice-podcast generation are scaffolded as future provider slots.

## Under the hood

```
   browser  ──┐
              │  REST + WS                ┌──────── 33 templates
   SDKs    ──►│                           │
              ▼                           │
       ┌──────────────────────────────┐   │     STT ─► LLM ─► TTS  (Pipeline mode,
   ────│ openvox daemon  :8000        │◄──┘     ──────────────────  sentence-streamed,
   │   │ FastAPI + Python core        │            sub-300 ms first-byte)
   │   │ + bundled Next.js dashboard  │
   │   │ + per-session orchestrator   │         OpenAI Realtime WS
   │   └──────────┬───────────────────┘         ──────────────────  (S2S mode,
   │              │                                                 sub-150 ms first-byte)
   │              ├────► SQLite (default) | Postgres
   │              ├────► ~/.openvox/storage (default) | TOS / S3 / GCS / OSS / MinIO
   │              └────► silero VAD (local ONNX) — server-side barge-in
   │
   ├─ phone     ── Twilio Media Streams
   ├─ WhatsApp  ── Business Cloud API (+ optional Personal QR bridge)
   ├─ Telegram  ── inbound polling or webhook · outbound /telegram/send
   └─ WeChat    ── Work / Lark audio bridge
```

A pipx (or Homebrew) install runs **one process** — the FastAPI daemon at `:8000` —
which also serves the static-built Next.js dashboard from the same port. No
separate gateway, no Redis. The Docker compose stack adds Postgres + a dev-mode
dashboard on `:3000` for hot-reload, but the production-shaped path is the
single-binary daemon.

The orchestrator is a `VoiceSession` per call. Pipeline mode streams mic audio
to STT, feeds final utterances into the LLM, and forwards token streams to TTS
in **sentence-sized chunks** so the user hears the first word within ~300 ms.
S2S mode replaces the three-provider chain with a single WebSocket to OpenAI
Realtime, dropping first-byte latency to ~120 ms. Skills + MCP tools work in
both modes.


## Extend it

### Add a skill

```python
# ~/.openvox/skills/get_weather.py
from openvox.skills import BaseSkill, SkillContext

class GetWeather(BaseSkill):
    id = "get_weather"
    description = "Look up current weather for a city."
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    async def run(self, args, ctx: SkillContext):
        return {"city": args["city"], "temp_c": 21}
```

That's it — drop the file in `~/.openvox/skills/` and the daemon hot-reloads it within
a second (see `openvox/skills/loader.py` for the watcher). Attach the skill to any agent
on the Skills tab.

### Add a provider

Implement `STTProvider`, `TTSProvider`, `LLMProvider`, or `RTCProvider` from
`openvox.providers.base`, then either register at startup or expose via the
`openvox.providers` setuptools entry-point.

### Use the SDK

> **Heads-up:** the TypeScript + Python SDK packages aren't published yet
> (clone the repo and link locally — `packages/sdk-ts/` and `packages/sdk-py/`).
> The REST + WebSocket API at `localhost:8000` is stable and documented; if
> you want to talk to the daemon today, the HTTP path is the canonical one
> until the SDKs ship.

```ts
import { OpenVoxClient, VoiceSession } from "@openvox/sdk";

const client = new OpenVoxClient("http://localhost:8000");
const agent = await client.templates.instantiate("ecommerce-support");

const sess = new VoiceSession({
  baseWsUrl: client.wsUrl(),
  options: { agentId: agent.id },
});
sess.on(ev => console.log(ev));
await sess.start();
```

```python
from openvox_sdk import OpenVoxClient, VoiceSession, VoiceSessionOptions

client = OpenVoxClient("http://localhost:8000")
agent = client.instantiate_template("education-tutor")

async with VoiceSession("http://localhost:8000", VoiceSessionOptions(agent_id=agent["id"])) as s:
    async for event in s.events():
        print(event)
```

## Repo layout

```
openvox/
├── apps/dashboard/                 Next.js 14 dashboard (App Router, Tailwind)
├── packages/
│   ├── core/                       Python — voice pipeline, providers, skills,
│   │                               FastAPI + WebSocket server, S2S bridge,
│   │                               33 templates, scheduler, observability.
│   │                               Ships as the `openvox-core` wheel on PyPI.
│   ├── cli/                        Thin `openvox` CLI (delegates to core).
│   ├── sdk-ts/                     TypeScript SDK (not yet on npm).
│   ├── sdk-py/                     Python SDK (not yet on PyPI).
│   ├── sdk-web/                    Browser-side voice helpers.
│   └── whatsapp_personal_bridge/   Node sidecar for WhatsApp Personal QR.
└── docker-compose.yml              core + dashboard + postgres (+ ngrok / whatsapp)
```

## License

Apache-2.0. Bring your own keys, ship your own agents.
