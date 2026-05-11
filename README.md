<div align="center">

# OpenVox

**Open-source, local-first voice agent platform.**

Build, test, and deploy production-grade voice agents — across web, phone, WhatsApp, and Telegram —
on your laptop. Apache-2.0.

[Dashboard](#) · [SDK](#) · [Templates](#templates) · [Architecture](docs/architecture.md)

</div>

---

## Why OpenVox?

Most agent-builder platforms today are LLM-only. The few voice-first options are either closed
SaaS or opinionated SDKs that lock you to a single vendor. OpenVox is the **OpenClaw of voice
agents**: it ships every layer of the stack, all the major providers, a sleek dashboard for
non-technical users, and an SDK + CLI for engineers.

- 🎯 **Sub-300 ms** end-to-end latency (sentence-level token streaming).
- 🔌 **Pluggable providers** — BytePlus, ElevenLabs, Deepgram, OpenAI, Anthropic, Gemini,
  Cartesia, AssemblyAI, Whisper, plus phone and WhatsApp.
- 🏠 **Local-first** — SQLite + filesystem out of the box. No telemetry. Your audio stays put.
- 🧰 **Extensible** — drop a Python file in `~/.openvox/skills/`, ship a pip package, or wire a
  third-party tool.
- 🧪 **Live playground** — talk to your agent in the browser with one click.
- 📞 **Multi-channel** — same agent over web RTC, phone (Twilio), WhatsApp, Telegram.
- 🛡️ **GDPR-aware** — configurable retention, residency, transcript-only mode, PII masking.

## Quick start

```bash
# 1. Configure (optional — defaults work locally)
cp .env.example .env
$EDITOR .env       # add at least BYTEPLUS_LLM_API_KEY + BYTEPLUS_VOICE_API_KEY

# 2. Run
docker compose up --build

# 3. Open the dashboard
open http://localhost:3000
```

That's it. The dashboard ships with four pre-built templates — pick one and you have a working
voice agent in under a minute.

## What ships in the box

### Pre-built templates

| Template                  | What it does                                                |
| ------------------------- | ----------------------------------------------------------- |
| **E-commerce support**    | Order lookups, returns, stock checks                        |
| **Science & Math tutor**  | Concept explanations + worked-example calculator            |
| **Stock analyst**         | Live quotes + technical indicators                          |
| **Voice analyzer**        | Sentiment + profanity + summarisation of recorded audio     |

### Providers

| Layer       | Default              | Alternatives                                           |
| ----------- | -------------------- | ------------------------------------------------------ |
| **LLM**     | BytePlus Seed-2.0    | OpenAI, Anthropic, Gemini, DeepSeek                    |
| **STT**     | BytePlus Seed ASR    | Deepgram, AssemblyAI, Whisper                          |
| **TTS**     | BytePlus Seed-Speech | ElevenLabs, Cartesia, OpenAI TTS                       |
| **RTC**     | BytePlus RTC         | (browser fall-back to direct WS streaming)             |
| **Phone**   | Twilio               |                                                        |
| **Chat**    | WhatsApp Business    | Telegram                                               |
| **Storage** | Local FS             | BytePlus TOS, AWS S3 / MinIO, GCS, Alibaba OSS         |

VAD, speech-to-speech, live interpretation, and voice-podcasts are exposed as
*roadmap providers* in the dashboard — BytePlus placeholders alongside working
alternatives (Silero VAD, OpenAI Realtime).

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
