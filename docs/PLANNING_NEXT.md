# Planning — next session

Picked up from end-of-day on Session 4. Four open questions, plus what I found while
researching them.

---

## 1. MCP (Model Context Protocol) — strongly recommend adding

### Status today
We don't have MCP. We have an OpenVox-internal skill SDK (`BaseSkill` Python class
with JSON-schema parameters) — good for *us* but doesn't let users connect to the
existing ecosystem of MCP servers (file system, GitHub, Slack, Postgres, Linear,
Stripe, etc.).

### What OpenClaw does
OpenClaw ships an **"MCP Registry"** as a first-class feature. Their docs nav lists
it under "Integrate external tools." If we want feature parity with the OpenClaw
positioning, MCP is table-stakes.

### What MCP gives us
The Model Context Protocol (Anthropic-led, standardised late 2024) is a small
JSON-RPC spec for connecting LLM apps to external tools and data sources. An MCP
*server* exposes:
- **Tools** (callable functions with JSON schema, isomorphic to our `BaseSkill`)
- **Resources** (read-only data the LLM can attach to context)
- **Prompts** (templated prompts the user can invoke)

The MCP *client* (our core service) connects to one or more servers and surfaces
their tools to the LLM. There's already a large public registry of MCP servers — by
adding a client we get hundreds of integrations for free.

### Implementation plan (~1 day)

1. **New module** `packages/core/openvox/mcp/`:
   - `client.py` — JSON-RPC over stdio, SSE, or websocket. Use Anthropic's
     reference Python SDK (`mcp` on PyPI) — do NOT roll our own.
   - `bridge.py` — a small adapter that wraps an MCP tool as a `BaseSkill`. At
     agent-config time we connect to the configured MCP servers, list their tools,
     and synthesise a `BaseSkill` per tool on the fly.
2. **Config**: per-agent `mcp_servers: list[dict]` field on `Agent` model.
   ```json
   [{"name": "github", "transport": "stdio",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}}]
   ```
3. **Dashboard**: new "MCP servers" tab on the agent edit page. Form to add a
   server (URL or command). Add "Browse registry" link to Anthropic's public list.
4. **Lifecycle**: spin up MCP server processes on demand (one per session); cache
   tool schemas; tear down when session ends.
5. **Skill translation**: tool calls from the LLM that match an MCP tool route
   through `mcp/bridge.py` instead of the local skill registry.

### Effort
- Backend: 4–5 hrs (mostly stdio/SSE plumbing).
- Frontend: 2 hrs (config form + tool list display).
- Total: ~1 day.

---

## 2. Do we need a plugin framework?

Short answer: **we already have most of one — but it's incomplete vs OpenClaw**.

### What we have today
- **Skills**: drop a `.py` in `~/.openvox/skills/`, or pip-install via the
  `openvox.skills` entry-point group. Auto-discovered at startup.
- **Providers**: same pattern via `openvox.providers` entry-point.
- **Templates**: in-process Python list (`api/routes/templates.py`).

### What OpenClaw has that we don't
- **ClawHub** — a managed registry/marketplace where users browse & install skills
  with a click. Their docs reference "bundled / managed / workspace skills" which
  mirrors npm/pip's concept but for agent capabilities.
- **Hot-reload** — drop a new skill, no restart needed.
- **MCP** (covered in §1).

### Recommendation
Add MCP first (§1) — that single change opens up the ecosystem of pre-built tools
without writing any registry yourself. Then incrementally:

1. **Hot-reload skills**: file watcher on `~/.openvox/skills/`, reimport on change.
   Small win, ~2 hrs.
2. **Skill browser UI**: a "Skills marketplace" page that lists popular MCP
   servers and `pip install` snippets. No backend marketplace needed initially —
   just curated links. ~3 hrs.
3. **First-class workflow tools** like OpenClaw's `browser`, `canvas`, `cron`,
   `sessions`, `Discord/Slack` — implement these as built-in skills. The most
   useful for voice are `cron` (covered in §3) and `webhooks` (so agents can be
   triggered by external events).

We do NOT need to build our own marketplace right now. MCP + the existing
entry-point system covers ~90% of the use cases.

---

## 3. Task scheduling — design

### Use cases the user named or implied
- "Every night at 8 PM, run the audio analyzer on yesterday's recordings"
- "On Monday at 9 AM, call the top 10 leads and qualify them"
- "When a new file lands in `/uploads`, transcribe and summarise"
- "Every hour, sync new orders into the e-commerce agent's KB"

### OpenClaw reference
"Cron jobs" is one of their first-class tools, listed under
`docs.openclaw.ai/automation/cron-jobs`. Cron syntax + invoke an agent.

### Recommended design

**Backend (~half day)**:
- **New module** `packages/core/openvox/scheduler/`:
  - `engine.py` — wraps `apscheduler` (`AsyncIOScheduler`); persistent jobstore
    backed by our existing SQLAlchemy DB.
  - `triggers.py` — supports `cron` (e.g. `"0 20 * * *"`), `interval`
    (e.g. `every 30 minutes`), and `webhook` (an HTTP endpoint that fires a job
    on POST).
  - `runner.py` — knows how to execute three job kinds:
    1. **Invoke an agent** — open a one-shot text or voice session, run a
       prompt template, save the transcript.
    2. **Run a skill directly** — useful for "transcribe everything in this
       folder."
    3. **Outbound call** — Twilio + the existing pipeline.
- **New tables**:
  ```python
  class ScheduledJob:
      id, name, agent_id (nullable), kind (agent_invoke|skill|call|webhook),
      trigger_type (cron|interval|once|webhook), trigger_expr,
      payload_json, enabled, created_at, last_run_at, next_run_at, error
  class JobRun:
      id, job_id, started_at, ended_at, status, result_json, error
  ```
- **New routes** at `/api/v1/jobs`: list/create/update/delete + `/runs` history.

**Dashboard (~3 hrs)**:
- New top-level page **`/dashboard/schedules`**: cron picker, agent selector,
  payload editor, run history viewer.
- On each agent's edit page, new **"Schedule"** tab to create jobs scoped to that
  agent.

**Why APScheduler over Celery / Temporal**:
- Local-first ethos: APScheduler runs in-process, persists state in our SQLite
  or Postgres, no extra services.
- Cron + interval triggers cover 95% of use cases.
- ~3 MB dependency, well-maintained.

### Effort
- Backend: 5–6 hrs.
- Frontend: 3 hrs.
- Total: ~1 day.

---

## 4. Three popular voice-agent templates to add

Pulled from ElevenLabs Conversational AI's featured-vertical list (the dominant
market segments today). What I'd add to OpenVox, ranked by demo impact ×
implementation cost:

### A. Receptionist / appointment scheduler  ⭐ top pick
The single most common voice agent in production today. ElevenLabs lists six
variants on their main page (medical, legal, real-estate, hotel, after-hours…).

**Skills**: `lookup_calendar(date)`, `book_appointment(name, phone, slot)`,
`cancel_appointment(id)`, `business_hours()`, `transfer_to_human()`.
**Channels**: web RTC + Twilio inbound. Demo flow: "I'd like to book a haircut
next Tuesday after 2 PM" → agent finds open slot, confirms name + phone, books.

**Why us**: showcases tool-calling against a calendar (Google Calendar via MCP
once §1 lands), works in any vertical with light prompt edits.

### B. Outbound lead qualifier (sales SDR)
The high-value B2B use case. Calls a list of leads, asks BANT-style questions,
scores the lead, hands off to a human if qualified.

**Skills**: `fetch_next_lead()`, `record_disposition(score, reason)`,
`schedule_human_followup(slot)`, `send_followup_email(template)`.
**Channels**: Twilio outbound dialer + MCP for CRM (HubSpot/Salesforce). Demo
flow: agent dials a phone number, runs a BANT script, writes back to the CRM.

**Why us**: demonstrates outbound calling (we already have Twilio webhook scaffold)
and shows OpenVox can do regulated business workflows. CRM integration via MCP
makes this feasible without writing our own connectors.

### C. Multilingual customer support / IVR
Replaces traditional "press 1 for English, 2 for…" menus with a single voice
agent that detects language and answers natively. **BytePlus Seed ASR supports 51
languages** — this template is uniquely well-suited to our default stack.

**Skills**: `detect_language(audio)`, `route_to_specialist(topic)`,
`fetch_account_info(phone)`, `escalate_to_human()`. Re-uses `query_documents`
for FAQ-style answers.
**Channels**: phone (Twilio) primary. Set `voice_language` per detected lang at
runtime; pick a TTS speaker that matches.

**Why us**: showcases a BytePlus capability (multi-lang ASR) other platforms
can't match without paying for an extra translation model. Differentiator for
the README and demos.

### Templates we already have (don't re-add)
- E-commerce customer support
- Education / math & science tutor
- Stock analyst
- Voice recording analyzer
- Document Q&A assistant

### Effort
~2 hrs each (mostly: prompt + skill list + a couple of new built-in skills).
Total: ~6 hrs for all three.

---

## Suggested order for tomorrow

1. **Morning**: §3 task scheduling (foundational, unlocks recurring features).
2. **Afternoon**: §1 MCP integration (unlocks ecosystem of tools).
3. **Late afternoon**: §4 receptionist template (uses both above + tool-calling).
4. (Defer) §4 outbound SDR — needs Twilio outbound dialer; pick up after.
5. (Defer) §4 multilingual IVR — needs language-detection skill; pick up after.

Each top-priority item is ~1 day. If you want to compress: ship MCP first, then
templates, then scheduling — MCP makes templates significantly more capable.

---

## Open questions to settle before coding

1. **MCP server bundling**: should OpenVox ship a curated set of MCP servers
   (filesystem, http, sqlite) or just provide config UI and let users install via
   npm/pip? My suggestion: provide config UI only at first; ship a "Suggested
   servers" link list.
2. **Scheduler permissions**: should scheduled outbound calls have any safety
   gate (rate limit, manual approval the first time)? Strongly recommend yes —
   default to "preview" mode where the agent prepares the call but a human clicks
   "Send" until the user disables that gate.
3. **Templates: should we ship sample data?** E.g. for the receptionist, pre-load
   a fake calendar with available slots like we did with `_DEMO_ORDERS`. Saves a
   user from having to wire up Google Calendar before they can demo it. I'd say
   yes — keeps the local-first first-run delightful.
