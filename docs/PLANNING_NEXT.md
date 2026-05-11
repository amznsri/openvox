# Planning — next session

Updated end of Session 6.

**Shipped this session (Session 6):**
- ✅ Outbound lead qualifier (SDR) template + Twilio outbound dial-out + BANT skills.
- ✅ Multilingual customer-support IVR template + `detect_language` skill +
  `voice_map` per-language TTS routing.

**Carrying forward, in priority order:**

---

## 1. Scheduler webhook trigger — ~2 hrs

Event-driven jobs alongside cron / interval / once. Add
`trigger_type="webhook"` + auto-generate a token on creation. New route
`POST /api/v1/jobs/webhook/{token}` that fires the job (rate-limited;
ignored if `enabled=false`). Dashboard: copy-to-clipboard webhook URL.

Use case: external file-upload service POSTs a webhook → agent processes
the new file. Pairs naturally with `audio_batch` and `agent_query` kinds.

---

## 2. Skill hot-reload — ~2 hrs

Drop a `.py` in `~/.openvox/skills/` → discoverable mid-session, no restart.

- `watchfiles` (small async-friendly dep).
- On change, re-run `SkillRegistry._load_local_folder()`.
- Existing running `VoiceSession`s keep the old binding; new sessions
  pick up the new code.

---

## 3. Curated MCP server catalogue — ~3 hrs

The MCP integration works but users have to find servers themselves. Add
a "Browse" view on the MCP tab with a curated list:

- Filesystem (`@modelcontextprotocol/server-filesystem`)
- GitHub (`@modelcontextprotocol/server-github`)
- Postgres (`@modelcontextprotocol/server-postgres`)
- Slack, Linear, Stripe, Google Drive…

`packages/core/openvox/mcp/catalogue.json` served via
`GET /api/v1/mcp/catalogue`. Dashboard renders cards with one-click "Use
this server" that pre-fills the config form. No marketplace backend
needed.

---

## 4. CRM integrations via MCP (for SDR) — ~½ day

The SDR template uses an in-memory leads dataset for the demo. For real
deployments wire HubSpot or Salesforce. Two paths:

- **MCP**: ship a curated `mcp_servers` config snippet in the template's
  `default` so users can paste their `HUBSPOT_API_KEY` and go. The MCP
  servers list `@modelcontextprotocol/server-hubspot` and similar exist.
- **Native skill**: `openvox/skills/builtin/sales_hubspot.py` that wraps
  HubSpot's REST API directly. More code but no external process.

Recommend MCP — keeps openvox lean.

---

## 5. Long backlog — same as Session 5's plan

- VAD (Silero local + BytePlus when launched)
- Speech-to-Speech (OpenAI Realtime adapter)
- Live interpretation / translation pipeline
- Voice podcast generation
- BytePlus RTC client SDK wiring on browser
- WhatsApp Business / Telegram inbound message routing
- Alembic migrations (replace `_ADDITIVE_COLUMNS` shim once schema settles)
- Test suite (`packages/core/tests/` still empty)
- GCS / Alibaba OSS storage impls
- CLI: `deploy`, `logs`, `dev` subcommands
- Cloud-hosted multi-tenant mode + OAuth (scaffold present, disabled)

---

## Suggested order

1. **§1 webhook trigger** — small, high-leverage, pairs with everything.
2. **§3 MCP catalogue** — UX polish for the feature we already shipped.
3. **§2 hot-reload** — quality-of-life for skill authors.
4. (Defer §4 unless someone needs a real CRM today.)
