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

## Get started

New to this? Follow the four steps in order. On a **fresh Mac you start from
zero** — no Python, maybe no Homebrew — so Step 1 matters. If `python3 --version`
already prints 3.11 or newer, skip to Step 2.

### Step 1 — Prerequisites (one-time)

OpenVox is a Python program. You need **Python 3.11+** on your machine first —
the installer does *not* install Python for you.

<details open>
<summary><b>macOS</b></summary>

```bash
# 1a. Homebrew (macOS package manager) — skip if `brew --version` already works.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

#     IMPORTANT: when it finishes it prints two/three lines under "Next steps"
#     starting with `echo ... >> ~/.zprofile`. Run those lines (they put `brew`
#     on your PATH), or just quit and reopen Terminal. Then verify:
brew --version

# 1b. Python 3.11+ — skip if `python3 --version` shows 3.11 or newer.
brew install python@3.12
python3 --version

# 1c. (optional) pipx — ONLY if you want the pipx install method in Step 2.
brew install pipx
pipx ensurepath          # then quit + reopen Terminal so PATH updates
```
</details>

<details>
<summary><b>Linux</b></summary>

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
# Fedora
sudo dnf install -y python3 python3-pip
# (optional) pipx
python3 -m pip install --user pipx && python3 -m pipx ensurepath
```
</details>

<details>
<summary><b>Windows</b></summary>

Install **Python 3.11+** from [python.org](https://www.python.org/downloads/) and
tick **"Add python.exe to PATH"** in the installer. Then, in PowerShell:

```powershell
python -m pip install --user pipx
python -m pipx ensurepath     # reopen PowerShell afterwards
```
</details>

### Step 2 — Install OpenVox

**Pick ONE method and stick with it.** Mixing methods (e.g. installing with the
script and later running `pipx install`) creates two copies and confusing
"which version am I running?" errors. Method A is recommended.

<details open>
<summary><b>A. One-line installer (recommended)</b></summary>

```bash
curl -fsSL https://github.com/amznsri/openvox/releases/latest/download/install.sh | bash
```

It auto-detects the best backend: it uses **pipx** if you installed it in Step
1c, otherwise it creates its own isolated environment at `~/.openvox/venv`. No
compiling, ~45 seconds. **Re-running this exact command later is also how you
upgrade** — it remembers which backend it used.
</details>

<details>
<summary><b>B. pipx (if you already use pipx)</b></summary>

```bash
pipx install openvox-core
```

Use this *or* the one-liner — not both. (The one-liner already uses pipx
automatically when pipx is present, so there's no benefit to running both.)
</details>

<details>
<summary><b>C. pip</b></summary>

```bash
pip install openvox-core
```

Works everywhere, but on Homebrew/Debian Python you'll likely hit a PEP 668
"externally-managed-environment" error — in that case use a virtualenv
(`python3 -m venv ~/.openvox/venv && ~/.openvox/venv/bin/pip install openvox-core`)
or just use Method A.
</details>

<details>
<summary><b>D. Homebrew</b></summary>

```bash
brew install amznsri/openvox/openvox
```

The most "Mac-native" option, but slower (2–5 min — it compiles a few native
deps) than the one-liner. Same program either way.
</details>

### Step 3 — Put `openvox` on your PATH

The `openvox` command lives in `~/.local/bin`. If your first command fails with
**`zsh: command not found: openvox`**, that folder isn't on your PATH yet:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc        # or simply quit and reopen Terminal
openvox version        # should now print a version
```

(Homebrew installs in Method D don't need this — brew is already on PATH.)

### Step 4 — Start it

These commands are **identical no matter how you installed** — they manage the
background service:

```bash
openvox start      # start as an always-on background service; prints your dashboard URL
openvox status     # is it running? shows the dashboard URL
openvox stop       # stop the service
openvox restart    # restart (e.g. after adding a provider key from a file)
openvox logs -f    # follow the log at ~/.openvox/logs/openvox.log
```

Then open the dashboard URL it printed — usually
**<http://localhost:8000/dashboard>** (it auto-picks a free port if 8000 is busy,
so trust the printed URL or `openvox status`).

> Prefer to watch logs live in your terminal instead of running a background
> service? Use `openvox run` (foreground, `Ctrl-C` to stop). Don't run `start`
> and `run` at the same time — they'd fight over the port.

### Updating

The day-to-day commands above are the same for every install method — **only the
upgrade command differs by how you installed.** The easy button:

```bash
openvox upgrade               # auto-detects pipx / venv / Homebrew and updates
openvox stop && openvox start # restart to load the new version
```

If you're on an older build that doesn't have `openvox upgrade` yet, use the
command for your method:

| Installed with…            | Upgrade command                                            |
| -------------------------- | ---------------------------------------------------------- |
| One-line installer / venv  | re-run the installer, **or** `openvox upgrade`             |
| pipx                       | `pipx upgrade openvox-core`                                |
| pip                        | `pip install --upgrade openvox-core`                       |
| Homebrew                   | `brew update && brew upgrade openvox`                      |

> ⚠️ **Don't run `pipx upgrade openvox-core` unless you installed *with* pipx.**
> If the one-liner used its venv fallback, pipx doesn't own that install and
> errors with *"Package is not installed."* `openvox upgrade` always picks the
> right path.

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
   - The key applies immediately — refresh the page and the provider row should say **configured**.
     (If a row's badge doesn't turn green, run **`openvox restart`** once in your terminal and
     refresh again.) Keys are encrypted on your machine in `~/.openvox/openvox.db`; they never
     leave it except to the provider you chose.

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
> file? OpenVox drops a starter **`~/.openvox/.env.example`** on first run — copy it
> to `~/.openvox/.env`, fill in one LLM + one voice key, then `openvox stop &&
> openvox start`. That exact path is read regardless of how you launch OpenVox —
> `openvox run` **and** the background `openvox start` daemon. An exported shell env
> var wins over the file, which wins over the dashboard store.

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
