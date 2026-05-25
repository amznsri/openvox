# Planning — Session 18 (UX leap: native OAuth, lower-latency voice, GTM polish)

Created at the start of Session 18, post-v0.2.5 shipping a clean
`brew install` + Phase 5/6 of `PLANNING_SESSION17.md` fully merged.

> **Background.** Session 17 locked down the production foundation
> (124 unit + 6 e2e tests, Alembic migrations, error-message UX,
> install matrix across macOS / Linux / Windows / brew, 12-cell CI
> matrix). Six PyPI releases (v0.2.0 → v0.2.5) shipped along the way,
> mostly debugging the Homebrew install path. The platform is now
> demonstrably production-grade: tests gate every PR, the install
> story is the same across four package managers, brew install
> works end-to-end on a clean Mac in ~1 minute.
>
> Session 18's premise: **shift from foundation to user experience.**
> The platform is solid; the on-boarding ceremony for the most-asked
> capabilities (Gmail + Calendar) is still "create a Google Cloud
> project, enable the APIs, paste the Client ID and Secret into the
> MCP tab" — five steps that bounce every non-technical user. Fix
> that, ship one new differentiating feature (Speech-to-Speech), and
> polish the outward-facing story (landing page from the pitch deck
> content, screenshot refresh, recorded demo).

---

## Strategic context

Three decisions that shape this plan:

1. **Productivity templates are the wedge.** Email + calendar are
   universal — every person already uses Gmail. If we make
   "Connect Gmail" a one-click flow, we unlock the Executive
   Assistant / Email Assistant / Calendar Scheduler templates for
   anyone — non-technical founders, operators, individuals. Every
   other capability (skills, scheduling, multi-channel) trickles
   down from that gateway use-case.

2. **S2S as the technical differentiator.** Closed-SaaS competitors
   (Vapi, Retell) shipped Speech-to-Speech this year. Sub-300ms
   pipeline is good; S2S is better for the right use-cases. We
   already have an OpenAI Realtime adapter slot in the provider
   registry; this is wiring, not invention. Single phase of work
   makes OpenVox feature-comparable on the differentiator most VC
   audiences will ask about.

3. **Outward-facing story is lagging the product.** Pitch deck
   in PR #18 captures the capability story; the README, landing
   page, and screenshots don't. Real users land on README → landing
   → docs, not a deck. Phase 5 makes the deck content the primary
   surface.

What's NOT in scope (consciously deferred):

| Item | Why deferred |
|---|---|
| Multi-tenant / cloud-hosted | Adoption signal hasn't begun; local-first remains the right default. |
| Voice cloning UI | Provider matrix already covers ElevenLabs / BytePlus voice cloning by capability; UI polish can wait. |
| Live interpretation / podcast generation | Cool demos but no user has asked. |
| Channel completion (WhatsApp / WeChat audio inbound) | Blocked on real test credentials, not on dev work. Pick up when creds arrive. |
| PyInstaller WinGet | Same call as Session 17 — no real Windows user has bounced yet. |

---

## At a glance

| Phase | Goal | Calendar | Risk |
|---|---|---|---|
| **1** | Native Google OAuth — kill the Cloud Console expedition | ~5 days | Med — Google API verification can take 1-2 wks |
| **2** | People API integration — name-to-email lookup beyond Gmail history | ~1.5 days | Low — well-documented API |
| **3** | Speech-to-Speech adapter (OpenAI Realtime) | ~3 days | Med — new transport, new event shapes |
| **4** | GTM polish — landing page from deck content, screenshots, demo video | ~2 days | Low — pure docs/marketing |
| **5** | CRM via MCP catalogue (HubSpot + Salesforce) for SDR template | ~2 days | Low — same MCP pattern as Gmail/Calendar |
| **Total** | | **~13-14 days single-track**, +20-30% buffer → ~3 weeks | |

PRs stacked, merge order: 1 → 2 → 3 → 4 → 5.

After Phase 1+2: v0.3.0 (productivity templates work end-to-end with
one-click OAuth — major UX bump).

After Phase 3: v0.3.1 (S2S available as a provider).

After Phase 5: v0.3.2 (CRM-via-MCP rounds out the SDR template).

---

## Phase 1 — Native Google OAuth

**Goal:** A non-technical user clicks **Connect Gmail** on the
agent edit page, signs in via the standard Google consent screen,
returns to OpenVox with the integration live. Zero Cloud Console
involvement from the user.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 1.1 | **Google OAuth app registration** (one-time, by maintainer) | External: console.cloud.google.com → "Desktop app" type → `http://localhost:8000/oauth/google/callback` redirect URI |
| 1.2 | **OAuth flow routes** | NEW `packages/core/openvox/api/routes/integrations/google.py` — `GET /api/v1/integrations/google/start`, `GET /oauth/google/callback`, `DELETE /api/v1/integrations/google/disconnect` |
| 1.3 | **Token store** — extend secrets.py with `OAuthToken` model | MOD `packages/core/openvox/secrets.py` — store access_token + refresh_token + expiry + scopes, encrypted at rest; rotate refresh tokens on use |
| 1.4 | **Token-aware Gmail + Calendar skills** | NEW `packages/core/openvox/skills/builtin/google_workspace.py` — native Python wrappers around `gmail.users.messages.list/get/send`, `calendar.events.list/insert/update/delete`, etc. Read tokens from store. |
| 1.5 | **Dashboard "Connect Gmail" UI** | MOD `apps/dashboard/src/app/dashboard/agents/[id]/page.tsx` — new Integrations tab. "Connect Gmail / Calendar" button. Status display ("Connected as alice@gmail.com — disconnect"). |
| 1.6 | **Template migration** — Executive Assistant et al swap MCP servers for native skills | MOD `packages/core/openvox/api/routes/templates.py` — replace `_GMAIL_MCP` / `_GCAL_MCP` references in defaults with native skill IDs; keep MCP as fallback option |
| 1.7 | **Submit OAuth app for Google verification** | External — Gmail send + Calendar write are sensitive scopes; verification takes 1-2 weeks. Start early in the phase. |

### Sub-tasks

1. **(0.5 days)** Register the OAuth app in Google Cloud Console.
   Add the localhost redirect URI. Pull Client ID into a config
   constant.
2. **(1 day)** Build OAuth flow routes. PKCE flow (no client secret
   at runtime — Desktop app type). State parameter for CSRF.
3. **(1 day)** Extend secrets.py with OAuthToken storage +
   refresh-rotation logic.
4. **(1.5 days)** Native Gmail + Calendar skills. Mirror the MCP
   server's tool surface so prompts don't need to change radically.
   Use `google-api-python-client` (already a transitive dep of
   `google-cloud-storage`).
5. **(0.5 days)** Dashboard UI — Integrations tab, Connect button,
   status display, disconnect.
6. **(0.5 days)** Template migration — swap defaults, keep MCP as
   a documented alternative on the MCP tab.

### Verification

- Brand new user: install OpenVox, click "Build by voice", say "I
  want a personal assistant," instantiate Executive Assistant, click
  Connect Gmail, sign in with personal Gmail, talk to agent: "what's
  in my inbox today?" → reads back real emails.
- No Cloud Console screens seen by user.
- `openvox info` shows the token store has 1 active Google
  integration; tokens are encrypted at rest in SQLite.
- Disconnect button revokes via Google API + drops local tokens.

### Risks

| Risk | Mitigation |
|---|---|
| Google verification takes 2+ weeks | Submit on day 1 of Phase 1. Until verified, label as "unverified app" in consent screen — still works for the app's developers + manually-allowlisted testers. Ship the code, gate the public rollout on verification. |
| Refresh token rotation edge cases | Google rotates refresh tokens silently; library must handle the new-token-in-response case. Use the official `google-auth` library which does this. |
| Self-hosted users sharing one OAuth app | Each install authenticates the OpenVox-published app against the *user's* Google account. Tokens are per-user. Google's per-client quotas are high; we're nowhere near them. |
| Storing third-party tokens raises liability | Encrypted at rest via the existing `secrets.py` machinery (bug #77 wiring). Local-first means tokens never leave the user's machine. Document this clearly. |

---

## Phase 2 — People API for name-to-email lookup

**Goal:** "Schedule a meeting with John Doe" works even when the
user has never emailed John before. Looks John up in Google
Contacts, returns the email.

Builds directly on Phase 1's OAuth token store (same scopes,
same flow).

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 2.1 | **New scope on the OAuth consent** | MOD Phase 1's OAuth start route — add `https://www.googleapis.com/auth/contacts.readonly` |
| 2.2 | **`resolve_contact` skill** | MOD `packages/core/openvox/skills/builtin/google_workspace.py` — call People API `people.searchContacts` with the query; return name + email + photo |
| 2.3 | **Update Executive Assistant prompt** | MOD `_EXEC_ASSISTANT_PROMPT` in templates.py — try People API first, fall back to Gmail search (current Step C), fall back to asking the user |

### Sub-tasks

1. **(0.5 days)** Add the contacts scope. Update existing users'
   tokens — refresh flow handles the re-consent (or just prompt to
   reconnect).
2. **(0.5 days)** `resolve_contact` skill + tests.
3. **(0.5 days)** Prompt update — three-tier fallback: People API
   > Gmail search > ask user.

### Verification

- "Schedule a meeting with my dentist" — finds the dentist in
  Contacts even if no Gmail history exists.
- Falls through to Gmail search if contact not in People API.
- Falls through to asking the user if neither source has it.

### Risks

Low. People API is well-documented and stable.

---

## Phase 3 — Speech-to-Speech (OpenAI Realtime)

**Goal:** OpenAI Realtime as a provider option. Sub-300ms voice
pipeline becomes sub-150ms for agents that select S2S, with more
natural prosody than the STT→LLM→TTS pipeline.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 3.1 | **`S2SProvider` abstract base class** | NEW `packages/core/openvox/providers/s2s/base.py` — connect / push_audio / events generator |
| 3.2 | **OpenAI Realtime adapter** | NEW `packages/core/openvox/providers/s2s/openai_realtime.py` — WebSocket to OpenAI's realtime endpoint, handle session.update / input_audio_buffer.append / response.output_audio.delta events |
| 3.3 | **Orchestrator branch** — when agent has `s2s_provider` set, skip STT+LLM+TTS pipeline | MOD `packages/core/openvox/pipeline/orchestrator.py` — bypass the three-provider path; pump audio directly through S2S provider |
| 3.4 | **Agent.s2s_provider column + dashboard toggle** | MOD `packages/core/openvox/db/models.py` (additive column); MOD agent edit page (Voice tab: "Use Speech-to-Speech (lower latency, OpenAI Realtime)" toggle) |
| 3.5 | **Cost calculator update** | MOD `packages/core/openvox/pricing/` — S2S is priced differently (per-minute, all-in). Surface in observability. |

### Sub-tasks

1. **(0.5 days)** S2S base class design — what does the
   orchestrator need? Bidirectional audio + event stream.
2. **(1 day)** OpenAI Realtime adapter — WebSocket protocol, the
   ~10 event types we care about. Reuse our existing
   `make_async_client` for TLS-safety.
3. **(1 day)** Orchestrator branch — when s2s_provider set, take
   the bypass path. Skills still work (function-calling over the
   S2S event channel).
4. **(0.5 days)** Schema + UI + pricing.

### Verification

- Toggle S2S on an agent. First-audio latency drops from ~280ms
  to ~120ms (measured via `scripts/measure_interrupt.py` adapted).
- Interruption + barge-in still work (Realtime API has its own VAD).
- Skills still callable (OpenAI Realtime supports function calling).
- Cost displayed correctly in observability.

### Risks

| Risk | Mitigation |
|---|---|
| Realtime API is OpenAI-only — no provider portability | Documented; users who want S2S choose to lock to OpenAI for that agent. Other agents stay on the pipeline. |
| Function-calling shape differs from chat API | Adapter normalises both to the same `LLMResponseChunk.tool_calls` event shape. |
| Higher per-minute cost vs pipeline | Surface clearly in the agent setup UI ("S2S costs ~$0.06/min vs $0.02/min for the pipeline"). User decides. |

---

## Phase 4 — GTM polish

**Goal:** README, landing page, demo video, and screenshots all
match the pitch deck's capability story. First-time visitor to
GitHub or the local dashboard sees the same message.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 4.1 | **README rewrite** — pitch-deck content as the primary surface | MOD `README.md` — first 60 lines are the deck's "voice agents anyone can build" message. Install moves down. |
| 4.2 | **Landing page (`apps/dashboard/src/app/page.tsx`)** | MOD — use the pitch deck's content + visuals on the public landing. Templates carousel. "Talk to OpenVox" CTA front and centre. |
| 4.3 | **Screenshot refresh** | NEW `docs/images/screenshots/` — current dashboard at localhost:8000 (post-Phase 1), with real Gmail/Calendar integration. Replaces stale v0.1.8-era images. |
| 4.4 | **2-minute demo video** | NEW `docs/demos/build-by-voice.mp4` — screen recording of the Setup Assistant flow + first conversation with the built agent. Embed in README + landing. |

### Sub-tasks

1. **(0.5 days)** README rewrite.
2. **(1 day)** Landing page redesign — pull deck content into
   `page.tsx`. Match the deck's visual language.
3. **(0.5 days)** Screenshots — fresh, post-Phase 1 with OAuth in
   place.

### Verification

- New visitor to README understands what OpenVox does within 30
  seconds.
- Public landing CTA points at the build-by-voice flow.
- All screenshots reflect current dashboard + OAuth integration.

### Risks

Low. Pure docs/UI work.

---

## Phase 5 — CRM via MCP

**Goal:** The SDR template (`outbound-sdr`) is demoable without
mocking the CRM. HubSpot + Salesforce via curated MCP servers,
same one-click pattern as Phase 1's Google OAuth where possible.

### What gets built

| # | Deliverable | Files |
|---|---|---|
| 5.1 | **MCP catalogue entries for HubSpot + Salesforce** | MOD `packages/core/openvox/mcp/catalogue.json` — curated config for `@hubspot/mcp-server-hubspot` (when available) and the salesforce-mcp community server |
| 5.2 | **SDR template defaults to HubSpot** | MOD `templates.py` — `outbound-sdr` template's MCP defaults |
| 5.3 | **Dashboard MCP-tab one-click instantiation** | MOD agent edit page MCP tab — "Connect HubSpot" button that opens the auth flow for that MCP server |

### Sub-tasks

1. **(0.5 days)** Survey available HubSpot + Salesforce MCP servers.
   Pick the most-maintained one.
2. **(0.5 days)** Catalogue entries + template migration.
3. **(1 day)** Dashboard UI for one-click connect (similar to
   Phase 1's Connect Gmail).

### Verification

- Instantiate the SDR template, click "Connect HubSpot," sign in,
  the agent can list / update / log leads via voice.

### Risks

| Risk | Mitigation |
|---|---|
| Quality of community MCP servers varies | Pick well-maintained ones; document the alternative (custom Python skill) if MCP server is broken. |

---

## Cross-cutting work

### v0.2.6 polish (carry-forward from Session 17)

- Silence the orjson dylib `Failed to fix install linkage` warning
  in the Homebrew formula. Standalone PR, can land in parallel
  with Phase 1.

### Documentation deliverables

- `docs/integrations/google.md` — how to connect Gmail/Calendar
  (Phase 1).
- `docs/providers/s2s.md` — when to use S2S vs the pipeline
  (Phase 3).
- `docs/integrations/crm.md` — HubSpot / Salesforce setup (Phase 5).

### Decision points (open at planning time)

1. **Phase 1 — keep MCP as fallback or remove?** Recommendation:
   keep as a "for power users with their own Google Cloud project"
   path. Some users prefer it for compliance reasons (their own
   OAuth app = their own audit trail). Cost: one extra MCP tab
   subsection.

2. **Phase 3 — Realtime model selection.** OpenAI publishes multiple
   Realtime models (`gpt-4o-realtime-preview`, mini variants).
   Recommendation: default to mini for cost, expose model selector
   on the agent's Voice tab.

3. **Phase 5 — HubSpot vs Salesforce default.** Recommendation:
   HubSpot. Free tier is generous and the MCP server is more
   mature.

---

## Sequencing

```
v0.2.5 (current)
  │
  ├─ PR #N: v0.2.6 polish (orjson dylib)              ← parallel, low risk
  │
  ├─ Phase 1: Native Google OAuth                     ← gate on Google
  │           ├─ Submit OAuth app for verification    ←   verification
  │           ├─ Build flow (PR)                      ←   (1-2 weeks)
  │           ├─ Build native skills (PR)
  │           └─ Migrate templates (PR)
  │
  ├─ Phase 2: People API (1 small PR on top of Phase 1)
  │
  ├─ v0.3.0 release   ← Phase 1+2 done = productivity templates work
  │                     end-to-end without Cloud Console expedition
  │
  ├─ Phase 3: S2S (OpenAI Realtime)
  │
  ├─ v0.3.1 release   ← S2S available
  │
  ├─ Phase 4: GTM polish (parallel-safe with Phase 3 or 5)
  │
  ├─ Phase 5: CRM via MCP
  │
  └─ v0.3.2 release
```

Real-track work — verification dependency on Phase 1 means we can
start Phase 3 work in parallel while Google reviews the OAuth app.

---

## Shippable milestones

Each phase ships as a self-contained release. No "half-states" that
brick existing installs.

- After Phase 1+2: v0.3.0. **Headline:** "Connect Gmail with one
  click. Schedule meetings by saying 'meeting with John Doe' —
  the agent finds John's email."
- After Phase 3: v0.3.1. **Headline:** "Speech-to-Speech: sub-150ms
  voice latency for agents that opt in."
- After Phase 4: no version bump (docs/UI only). **Headline:**
  "Refreshed website + demo video."
- After Phase 5: v0.3.2. **Headline:** "Outbound SDR template
  connects to HubSpot in one click."

---

## Out of scope (deferred to later sessions)

- Multi-tenant / cloud-hosted offering. Local-first stays the
  default.
- WhatsApp Business / WeChat / Lark inbound *audio* (the text
  bridges already work — audio is gated on test credentials).
- Voice cloning UI polish (capability exists via ElevenLabs +
  BytePlus; better UI is a Session 19+ item).
- Live interpretation, podcast generation.
- PyInstaller WinGet path. Same call as Session 17.
- `homebrew-core` submission. Tap is fine for now.

---

## Handoff notes for the next session

When you (or a future Claude) pick this up:

1. **Read `CLAUDE.md` §8 first.** Sessions 16-17 added bugs #66-91;
   the dylib / venv / wheel ones in particular shaped the brew
   install path. Don't re-hit them.

2. **Check Google OAuth verification status before starting Phase
   1 UI work.** Submit the OAuth app for verification on Day 1 of
   the session; everything else proceeds while Google reviews.

3. **PR #18 (slide deck), #20 (Step C — name-to-email lookup),
   #13 (curl-bash positioning), #19 (gitignore) are open from
   Session 17 close.** Either merge them before starting Session
   18, or rebase Session 18 work on top.

4. **The user's local install** at the time of Session 18 start:
   pipx v0.1.8 (active daemon) + brew v0.2.5 (installed but
   shadowed by PATH). When testing native OAuth, use either, but
   pin to one for the duration of a test session — the shared
   `~/.openvox/openvox.db` will have an `alembic_version` row
   only if v0.2.5 has ever started.

5. **The pitch deck content** ([PR #18](https://github.com/amznsri/openvox/pull/18))
   is the source of truth for the Phase 4 GTM polish work. The
   user iterated on it 4 times; the final shape is the headline +
   subhead + chip pattern shown there.
