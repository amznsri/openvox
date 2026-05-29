<div align="center">

# OpenVox

**The open-source platform for building personal and production voice agents.**

One prompt or one click in — a working voice agent out. Pipeline or speech-to-speech,
on web, phone, WhatsApp, and Telegram. Self-host on a laptop in 60 seconds. Apache-2.0.

[Quick start](#quick-start) · [Feature highlights](#feature-highlights) · [Templates](#33-production-quality-templates) · [Provider matrix](#provider-matrix) · [Architecture](#architecture)

</div>

---

## Overview

OpenVox is an open-source platform that ships **every layer of the stack** — voice pipeline,
dashboard, SDK, CLI, 33 runnable templates, observability, and scheduling. A non-technical operator
builds a working agent in five minutes; a developer drops into Python or TypeScript to extend any
piece.

### How to use

| You want to… | Do this |
|---|---|
| **Try it in 60 seconds** | `curl …/install.sh \| bash` then `openvox start` |
| **Build an agent, no code** | Dashboard → Templates → *Copy* → *Test* |
| **Build by voice** | Dashboard → New agent → *"Build by voice"* and describe it |
| **Reach users by phone** | Connect Twilio on the agent's Channels tab |
| **Add custom logic** | Drop a Python skill in `~/.openvox/skills/` |
| **Embed in your own app** | Hit the REST + WebSocket API at `localhost:8000` |

---

## Feature highlights

### 🌍 Multilingual out of the box
41 voices and 7 first-class languages (English, Mandarin, Cantonese, Spanish, Bahasa, French,
Hindi) ship configured. The multilingual-support template covers 51+ languages via runtime
language detection — no "press 1 for…" menus.

### ⚡ Two voice modes per agent
Choose **Pipeline** (STT → LLM → TTS, ~300 ms first-byte, full provider mix-and-match) or **S2S**
Speech-to-Speech (single WebSocket, ~120 ms first-byte, native function-calling). Toggle per
agent on the Voice tab — if S2S is unavailable at call time it falls back to pipeline automatically.

### 🎙️ Build by voice
Describe the agent you want out loud; the built-in setup assistant picks a template, names it,
configures the voice, and walks you through publishing. Or use the dashboard form. Or the API.

### 🔌 Provider-agnostic
Anthropic · AssemblyAI · BytePlus · Cartesia · Deepgram · DeepSeek · ElevenLabs · Gemini · OpenAI ·
Whisper. Pick per agent, mix per layer — the LLM, STT, and TTS are independent slots.

### 🧰 Extensible
Drop a Python skill in `~/.openvox/skills/` and the daemon hot-reloads it within a second. Register
a custom provider, or wire any third-party tool via Model Context Protocol (MCP).

### 🧪 Playground, observability, and evals
Talk to your agent live in the browser, replay any past session turn-by-turn, and score regressions
against personas + recordings before you ship a prompt change.

### ⏰ Built-in scheduler
Cron, interval, one-off, or webhook triggers — no external job runner. A simple-mode UI lets
non-technical operators schedule outbound calls, digests, or batch jobs.

### 🏠 Self-hosted and GDPR-aware
SQLite + filesystem out of the box, no telemetry. Audio + text travel only to providers you
configure. Configurable retention, data residency, transcript-only mode, and PII masking are all
env-flag toggles. Provider keys are encrypted at rest.

---

## Quick start

### Install

```bash
curl -fsSL https://github.com/amznsri/openvox/releases/latest/download/install.sh | bash
openvox start
```

~45 seconds end-to-end. The installer detects Python 3.11+, picks `pipx` (or a venv fallback),
drops the `openvox` binary on `$PATH`, then `openvox start` runs OpenVox as a background service
and **prints your dashboard URL** — usually <http://localhost:8000/dashboard>, but if port 8000 is
already taken it auto-picks a free port and tells you which. No compile step, no Docker required.

<details>
<summary><b>Alternative installers</b> (pipx · pip · Homebrew · Windows)</summary>

```bash
# Already use pipx / pip? — works on macOS, Linux, Windows
pipx install openvox-core
pip install openvox-core      # ditto, but conflicts with PEP 668 systems

# Homebrew on macOS — slower (compiles from source) but more brew-native
brew install amznsri/openvox/openvox
```

- **Homebrew speed:** brew installs from sdists (compiles ~5 native deps via clang + rust), so
  expect 2-5 min vs the one-liner's ~45s. Same binary; only the install path differs.
- **Windows:** pipx is supported (`pipx install openvox-core`); a self-contained `.exe` via WinGet
  is on the roadmap once a code-signing cert is in place.

</details>

### Run it: `start` vs `run`

**Most people want `openvox start`** — it runs OpenVox as an always-on background service
(launchd on macOS, systemd on Linux, Windows Service), survives terminal closes, and restarts
at login. It prints your dashboard URL and returns you to the prompt.

```bash
openvox start            # ← background service. The one you usually want.
openvox status           # is it running? prints the dashboard URL
openvox stop             # stop the service
openvox logs -f          # tail ~/.openvox/logs/openvox.log
openvox restart          # restart (after changing a provider key, etc.)
```

Use **`openvox run`** instead only when you want the server in the **foreground** with live logs
in your terminal (handy for debugging) — `Ctrl-C` stops it, and it closes when you close the
terminal. Don't run both at once; they'd fight over the same port.

```bash
openvox run              # foreground; Ctrl-C to stop. Auto-opens the dashboard.
openvox info             # resolved config, secrets redacted
```

> **Which port?** Both commands bind 8000 by default, but auto-switch to the next free port if
> it's taken — so always open the URL they print (or run `openvox status`), not a hard-coded
> `:8000`. The chosen port is remembered across restarts.

### Your first agent (~3 minutes, no coding)

Once `openvox start` is running, **everything below happens in your browser** — you won't need
the terminal again except for the one `openvox restart` in step 2.

> The links assume the default port **8000**. If `openvox start` printed a different port (it does
> that automatically when 8000 is busy), use that one instead — `openvox status` always shows the
> current URL.

**1. Open the dashboard.**
Go to **<http://localhost:8000/dashboard>**. The left sidebar is your home base: *Agents*,
*Templates*, *Playground*, *Settings*.

**2. Add one AI provider key.** This is the only required setup — it's what powers the agent's
brain (and voice). You need **at least one**.

   - Click **Settings** in the sidebar. You'll see a list of providers, each marked
     *configured* or *missing key*.
   - Click a provider's row, paste your key, and hit **Save**:
     - **Easiest:** **BytePlus** — a single key covers the LLM *and* the voice, so one paste gets
       you fully working. Create one in the
       [BytePlus ModelArk console](https://console.byteplus.com/ark/region:ark+ap-southeast-1/apiKey).
     - **Already have OpenAI?** Paste it in the **OpenAI** row instead — get a key at
       <https://platform.openai.com/api-keys>. (Anthropic, Gemini, Deepgram, ElevenLabs, etc. all
       work the same way.)
   - Back in your terminal, run **`openvox restart`** once so the key loads, then refresh the
     page — the provider row should now say **configured**. (Keys are encrypted on your machine in
     `~/.openvox/openvox.db`; they never leave it except to the provider you chose.)

**3. Create an agent from a template.**

   - Click **Templates** in the sidebar → browse the 33 ready-made agents → click **Copy
     template** on one.
   - Good first picks that work with just one key: **E-commerce Support** or **Multilingual
     Support**. (Email Assistant / Calendar Scheduler need a Google account connected first, on
     the *Integrations* tab — skip those for your first try.)
   - Your new, fully-editable agent opens.

**4. Talk to it.**

   - Click **Test** (top-right) → the **Playground** opens.
   - **By voice:** click the big **Tap to talk** button, allow microphone access, and just speak.
     Say *"Stop"* any time to cut the agent off mid-sentence.
   - **Prefer to type?** Switch to the **Text** tab and send a message — no microphone needed.
     This is the quickest way to confirm everything works.

**That's your first working agent.** 🎉 From here you can:
- rewrite its personality + instructions on the **Behaviour** tab (the system prompt),
- swap its **voice** on the *Voice & model* tab,
- upload PDFs for it to answer from on the **Documents** tab,
- or put it on a phone number / WhatsApp / Telegram from the **Channels** tab.

---

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

Click **Copy template** to instantiate any of them. Each copy is fully editable — change the system
prompt, swap the voice, attach documents, add skills, then publish.

### Provider matrix

Provider-neutral by design. Mix and match per agent through the dashboard or `.env`:

| Layer       | Supported providers                                                                                       |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| **LLM**     | Anthropic · BytePlus · DeepSeek · Gemini · OpenAI · *(any OpenAI-compatible endpoint)*                    |
| **STT**     | AssemblyAI · BytePlus · Deepgram · Whisper *(local or hosted)*                                            |
| **TTS**     | BytePlus · Cartesia · ElevenLabs · OpenAI TTS                                                             |
| **S2S**     | OpenAI Realtime *(single-WS replacement for the STT → LLM → TTS chain)*                                   |
| **VAD**     | Silero *(local ONNX, server-side barge-in)*                                                               |
| **RTC**     | BytePlus RTC *(optional; the default voice path is plain browser WebSocket → daemon at :8000)*            |
| **Phone**   | Twilio Media Streams                                                                                      |
| **Chat**    | WhatsApp Business · WhatsApp Personal *(QR sidecar)* · Telegram *(inbound + outbound)* · WeChat Work + Lark *(scaffolded — callback wiring done, crypto pending real-account testing)* |
| **Storage** | Local filesystem · AWS S3 · MinIO *(via S3-compatible endpoint)* · BytePlus TOS                           |

> **First-run tip:** BytePlus gives you LLM + STT + TTS under a single API key — the
> lowest-friction way to try OpenVox. Swap any layer per agent once you're up.

> **Where keys live:** the dashboard (Settings / Setup) stores keys encrypted at
> `~/.openvox/openvox.db` — the simplest path, works in every run mode. Prefer a
> file? Put them in **`~/.openvox/.env`** (e.g. `OPENAI_API_KEY=sk-…`). That exact
> path is read on startup regardless of how you launch OpenVox — `openvox run`
> **and** the background `openvox start` daemon. An exported shell env var wins
> over the file, which wins over the dashboard store. Editing the file or env vars
> takes effect after `openvox stop && openvox start`.

Live interpretation and voice-podcast generation are scaffolded as future provider slots.

---

## Architecture

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
   │              ├────► ~/.openvox/storage (default) | BytePlus TOS / S3 / MinIO
   │              └────► silero VAD (local ONNX) — server-side barge-in
   │
   ├─ phone     ── Twilio Media Streams
   ├─ WhatsApp  ── Business Cloud API (+ optional Personal QR bridge)
   ├─ Telegram  ── inbound polling or webhook · outbound /telegram/send
   └─ WeChat    ── Work / Lark adapters scaffolded (crypto pending real creds)
```

A pipx (or Homebrew) install runs **one process** — the FastAPI daemon at `:8000` — which also
serves the static-built Next.js dashboard from the same port. No separate gateway, no Redis. The
Docker compose stack adds Postgres + a dev-mode dashboard on `:3000` for hot-reload, but the
production-shaped path is the single-binary daemon.

The orchestrator is a `VoiceSession` per call. **Pipeline mode** streams mic audio to STT, feeds
final utterances into the LLM, and forwards token streams to TTS in sentence-sized chunks so the
user hears the first word within ~300 ms. **S2S mode** replaces the three-provider chain with a
single WebSocket to OpenAI Realtime, dropping first-byte latency to ~120 ms. Skills + MCP tools
work in both modes.

---

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

Drop the file in `~/.openvox/skills/` and the daemon hot-reloads it within a second (see
`openvox/skills/loader.py` for the watcher). Attach it to any agent on the Skills tab.

### Add a provider

Implement `STTProvider`, `TTSProvider`, `LLMProvider`, or `RTCProvider` from
`openvox.providers.base`, then either register at startup or expose via the `openvox.providers`
setuptools entry-point.

### Use the SDK

> **Heads-up:** the TypeScript + Python SDK packages aren't published yet (clone the repo and link
> locally — `packages/sdk-ts/` and `packages/sdk-py/`). The REST + WebSocket API at `localhost:8000`
> is stable and documented; the HTTP path is the canonical way to talk to the daemon until the
> SDKs ship.

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

---

## Self-hosting & production

### Docker mode

```bash
git clone https://github.com/amznsri/openvox.git
cd openvox
cp .env.example .env
$EDITOR .env       # add at least one LLM key + one voice key

docker compose up --build
open http://localhost:3000
```

Brings up four services: core + dashboard + postgres (+ optional `--profile whatsapp` for WhatsApp
Personal or `--profile tunnel` for ngrok).

> **Upgrading from a v0.1.x install?** Old installs used a Node gateway + Redis, both deleted in
> Phase 1. Run `docker stop openvox-server openvox-redis && docker rm openvox-server openvox-redis`
> once to clear the orphan containers before `docker compose up --build`.

### Repo layout

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

---

## License

Apache-2.0. Bring your own keys, ship your own agents.
