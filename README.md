<div align="center">

# OpenVox

**The open-source platform for building production voice agents.**

Web, phone, WhatsApp, Telegram. 41 voices, 7 languages out of the box. Pluggable providers — bring
your own LLM, STT, and TTS. Self-host on a laptop in 60 seconds. Apache-2.0.

[Dashboard](#) · [SDK](#) · [Templates](#templates) · [Architecture](docs/architecture.md)

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

Two install modes — pick the one that matches you. Both share the same
agents, skills, templates, and dashboard.

### Personal install (CLI mode)

Pick whichever path fits your machine — all four ship the same
`openvox` binary:

```bash
# A) PyPI — works on macOS, Linux, Windows
pip install openvox-core

# B) Curl-bash — macOS / Linux (auto-picks pipx or venv)
curl -fsSL https://github.com/amznsri/openvox/releases/latest/download/install.sh | bash

# C) Homebrew — macOS / Linux
brew install amznsri/openvox/openvox

# D) WinGet — Windows
winget install OpenVox.OpenVox
```

Then pick foreground or daemon mode:

```bash
openvox run              # foreground; Ctrl-C to stop. Auto-opens dashboard.
openvox start            # background daemon (launchd / systemd / Windows Service).
                         # Starts at login. Stop with `openvox stop`.
openvox status           # is it running?
openvox logs -f          # tail ~/.openvox/logs/openvox.log
```

`openvox info` shows resolved config (with secrets redacted) — the
fastest way to debug "is this thing configured?". See
[`docs/install.md`](./docs/install.md) for per-path details and
[`docs/install-cli.md`](./docs/install-cli.md) for the source-checkout /
contributor flow.

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
for ngrok). See [`docs/install-docker.md`](./docs/install-docker.md)
when written (Phase 4).

> **Upgrading an existing install?** If you're pulling the latest
> after a long pause, check [`docs/upgrade-notes.md`](./docs/upgrade-notes.md)
> — Phase 1 deleted the Node gateway + Redis services, which means a
> one-time orphan-container cleanup is required.

That's it. The dashboard ships with **29 ready-to-run templates** — pick one and you have a
working voice agent in under a minute. Or click **"Build by voice"** in the agent creator and
describe what you want.

## What ships in the box

### 29 production-quality templates

| Category          | Templates                                                                        |
| ----------------- | -------------------------------------------------------------------------------- |
| **Support**       | E-commerce support · Multilingual support (51+ languages) · Hotline (7 languages) |
| **Productivity**  | Email assistant · Calendar scheduler · Executive assistant (Gmail + Calendar)    |
| **Sales**         | Outbound SDR · Telesales (7 languages) · Reactivation (7 languages)              |
| **Knowledge**     | Document Q&A with RAG · Receptionist with in-app bookings                        |
| **Education**     | Science & Math tutor with worked-example calculator                              |
| **Finance**       | Stock analyst with live quotes + technical indicators                            |
| **Analytics**     | Voice analyzer — sentiment, profanity, summarisation                              |
| **Meta**          | Setup assistant — build new agents conversationally by voice                     |

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

Speech-to-speech, live interpretation, and voice-podcasts are scaffolded as future provider
slots in the dashboard.

## Architecture

```
                ┌──────────────────────────────┐
   browser ─►   │ Next.js dashboard (3000)     │
                └──────────────┬───────────────┘
                               │ REST + WS
                ┌──────────────▼───────────────┐
   SDKs   ────► │ Node gateway (3001)          │  auth, rate-limit, OAuth
                └──────────────┬───────────────┘
                               │ proxy + WS bridge
                ┌──────────────▼───────────────┐
   phone  ────► │ Python core (8000)           │  STT ↔ LLM ↔ TTS pipeline
   WhatsApp ──► │ FastAPI + asyncio + skills   │
                └──────────────┬───────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       Postgres + Redis    Storage          Providers
                       (local | TOS | S3 | …)   (plug-ins)
```

The core orchestrator is a single `VoiceSession` per call. It streams mic audio to the STT
provider, feeds final utterances into the LLM, and forwards token streams to the TTS provider
in **sentence-sized chunks** so the user hears the first word within a few hundred milliseconds.
Skill calls run inline through the LLM tool-use loop.

See [docs/architecture.md](docs/architecture.md) for the deep dive.

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

Or scaffold one:

```bash
npx openvox skills new get_weather
```

### Add a provider

Implement `STTProvider`, `TTSProvider`, `LLMProvider`, or `RTCProvider` from
`openvox.providers.base`, then either register at startup or expose via the
`openvox.providers` setuptools entry-point.

### Use the SDK

```ts
import { OpenVoxClient, VoiceSession } from "@openvox/sdk";

const client = new OpenVoxClient("http://localhost:3001");
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

client = OpenVoxClient()
agent = client.instantiate_template("education-tutor")

async with VoiceSession("http://localhost:3001", VoiceSessionOptions(agent_id=agent["id"])) as s:
    async for event in s.events():
        print(event)
```

## Repo layout

```
openvox-v2/
├── apps/dashboard/           Next.js 14 dashboard (App Router, Tailwind)
├── packages/
│   ├── core/                 Python — voice pipeline, providers, skills, API
│   ├── server/               Node.js — REST + WS gateway, telephony hooks
│   ├── sdk-ts/               TypeScript SDK
│   ├── sdk-py/               Python SDK
│   └── cli/                  `openvox` CLI
├── templates/                Built-in agent templates (catalogue lives in core/api/routes/templates.py)
├── docker-compose.yml
└── docs/
```

## License

Apache-2.0. Bring your own keys, ship your own agents.
