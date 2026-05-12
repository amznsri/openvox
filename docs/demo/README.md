# OpenVox — 60-second demo script

A spoken-word script for a one-minute project intro. Roughly 155 words
at a conversational pace; trim 10–15 words if you talk fast.

---

## The script

> **Hi, this is OpenVox** — an open-source, local-first platform for
> building production voice agents.
>
> One `docker compose up` spins up the whole stack: a Next.js
> dashboard, a Node gateway, a Python core that wires speech-to-text,
> a streaming LLM, and text-to-speech into a sub-three-hundred-
> millisecond conversation loop.
>
> Out of the box you get **eight templates** — receptionist, outbound
> SDR, multilingual support, document Q&A, stock analyst, audio
> analyzer, education tutor, and e-commerce support — plus
> **twenty-six built-in skills** the agent can call mid-conversation.
> Want more? Drop a Python file in your skills folder, or plug in any
> MCP server.
>
> The whole thing runs on BytePlus by default — Seed-2.0 LLM, Seed-Speech
> 2.0 TTS, Seed-ASR streaming — but every provider is swappable:
> OpenAI, Anthropic, Gemini, ElevenLabs, Deepgram, you name it.
>
> Built-in scheduler. Per-agent RAG. Twilio outbound. WhatsApp inbound.
> Full session telemetry.
>
> **Star us on GitHub — github.com/amznsri/openvox.**

---

## Key features (the bullet-point version)

If you want to tighten this into a written README intro instead of a
spoken script, lift these:

- **Local-first** — your audio and transcripts never leave your
  machine unless you opt in. SQLite + filesystem defaults; Postgres
  + S3/TOS work too.
- **Sub-300 ms first-audio latency** — async generators all the way
  down, sentence-flush TTS, real-time interruption.
- **14 providers shipped** — BytePlus, OpenAI, Anthropic, Gemini,
  DeepSeek (LLM); BytePlus, Deepgram, AssemblyAI, Whisper (STT);
  BytePlus, ElevenLabs, Cartesia, OpenAI (TTS); BytePlus RTC.
- **26 built-in skills** across 9 modules — order lookup, calendar
  booking, BANT scoring, sentiment + profanity analysis, web search,
  stock quotes, document Q&A, image analysis, language detection.
- **8 production templates** — instantiate a receptionist, SDR,
  multilingual IVR, or document Q&A agent in one click.
- **MCP support** — bridge any Model-Context-Protocol server (stdio
  or SSE) as per-agent tools. Filesystem, GitHub, Postgres, Slack
  all work out of the box.
- **Built-in scheduler** — cron / interval / once triggers across
  four job kinds: agent_query, skill_run, audio_batch,
  outbound_call_batch.
- **Per-agent RAG** — PDF + image extraction, embeddings with
  automatic BM25 fallback when the embedding endpoint is gated.
- **Telephony** — Twilio outbound dial-out, WhatsApp Business +
  Telegram webhook scaffolding.
- **Multilingual** — 51-language ASR auto-detect, per-language
  voice mapping via `Agent.voice_map`.
- **Full observability** — every session writes duration,
  first-token latency, turn count, transcripts, skill calls, and
  cost — all queryable from the dashboard.
- **Three-line extensibility** — skills, providers, and templates
  each have a single registration point. No framework gymnastics.

---

## Use cases — who's this for?

Concrete scenarios where OpenVox is the right tool:

### Customer support
- **Tier-1 phone IVR** that handles "where's my order", "I want a
  refund", "talk to a specialist" without a human in the loop.
- **51-language support line** — one agent auto-detects the caller's
  language and switches its voice to match.
- **Audio QA** — overnight job analyses yesterday's call recordings
  for sentiment + profanity + summary, posts a digest.

### Sales & lead-gen
- **Outbound SDR** that dials a CSV of leads, runs BANT
  qualification, books demos on the calendar, and writes
  dispositions back to your CRM via an MCP server.
- **Inbound qualifier** — picks up after-hours leads, does a
  conversational intake, hands off to a human in the morning.

### Internal tools
- **Document Q&A** — upload your runbooks / contracts / policy PDFs,
  ask questions by voice. RAG + vision for diagrams.
- **Daily ops digest** — scheduled `agent_query` job summarises
  yesterday's signals (logs, ticket queue, dashboards) and posts to
  Slack.

### Receptionist / front desk
- **Small business answering service** — books appointments, knows
  hours, transfers when needed.
- **Multi-location dispatch** — routes calls to the nearest open
  branch based on caller-id area code.

### Education & training
- **Conversational tutor** — explains concepts at user-specified
  difficulty levels, asks Socratic follow-ups.
- **Language practice partner** — multilingual voice, sentiment
  feedback, profanity guardrail.

### Healthcare & finance front-ends
- **Appointment booking + insurance pre-screen** for clinics.
- **Stock-analyst briefings** — live quote + news summary on demand;
  schedule daily 8 AM market open recaps.

### Developer & demo scenarios
- **Hackathon starter** — clone, drop API keys, voice agent running
  in five minutes.
- **Provider eval** — A/B BytePlus vs OpenAI vs Anthropic on the
  same prompt by toggling the LLM dropdown.
- **MCP showcase** — show off any new MCP server as a real voice
  tool in minutes, without writing a skill.

---

## Suggested ordering for the spoken delivery

If you record this:

1. **Hook (5s)** — "Open-source, local-first voice agents."
2. **What it does (15s)** — one command, the stack, the latency
   number.
3. **Templates + skills (15s)** — eight templates, twenty-six
   skills, plug-and-play extensibility.
4. **Provider story (10s)** — BytePlus default but everything
   swappable.
5. **Operational features (10s)** — scheduler, RAG, telephony,
   observability — name-drop and move on.
6. **Call to action (5s)** — GitHub link.

Hit five out of six and you're at 50–55 seconds, which is the
sweet spot for social-media autoplay.
