# Planning — Session 10 (Voice-driven Setup Assistant)

Locked 2026-05-18. Build *after* Session 9 ships completely (eval
dashboard UI, pricing-breakdown card, WeChat/Lark audio bridges,
real LLM token usage, scheduler webhook, skill hot-reload, and the
pending Telegram end-to-end test).

## What we're building

A built-in "setup-assistant agent" that lets non-technical users
*create another agent by talking to it.* The recursion is part of
the demo story ("build a voice agent by talking to one").

Two decisions locked by the user:

1. **Voice + text hybrid.** Same flow accepts mic input OR typed
   messages. Useful when the user is on a call already, or in a
   hands-busy moment, or when their accent fights with STT.
2. **First-class on the landing page.** Not a buried sub-menu.
   Specifically:
   - Public landing page (`/`) gets a "Try voice setup" CTA next to
     "Open dashboard".
   - Dashboard topbar "New agent" routes to `/dashboard/agents/new`
     which is now a *chooser* (Form / Voice), not a form directly.
   - Form route stays accessible at `/dashboard/agents/new?mode=form`
     for backward compatibility + power-user muscle memory.

## What it explicitly does NOT do

- **No API-key / token / webhook-URL dictation** — these stay
  form-only. The assistant tells the user what's still required at
  the end of the voice flow.
- **No MCP server configuration via voice** — same reason.
- **No multi-tenant / shared-agent scenarios** — the assistant
  operates on the calling user's own draft, not other people's.
- **No retroactive editing of *published* agents via voice** —
  scope creep risk. Voice flow only works while status="draft".
  Edit-by-voice on a published agent is Session 11+ if there's
  demand.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ /dashboard/agents/new?mode=voice                         │
│ ┌───────────────────────────┐ ┌────────────────────────┐ │
│ │ 🎙 Setup Assistant         │ │ Draft Agent (live)     │ │
│ │ (same /ws/voice protocol  │ │ Name:     ...          │ │
│ │  as the playground; the   │ │ Template: receptionist │ │
│ │  assistant is itself a    │ │ Voice:    en_male_tim… │ │
│ │  built-in template)       │ │ Greeting: ...          │ │
│ │                           │ │ Skills:   [list]       │ │
│ │ [Mic]   [Type a message]  │ │ ⚠️ Still needed:        │ │
│ │                           │ │  • Phone number        │ │
│ │ Hybrid mode = user can    │ │  • API keys (Twilio…)  │ │
│ │ click mic OR type. Either │ │  • MCP server tokens   │ │
│ │ sends a user_text frame.  │ │                        │ │
│ └───────────────────────────┘ └────────────────────────┘ │
│ [Save draft] [Publish] [Switch to form]                  │
└──────────────────────────────────────────────────────────┘
```

**Why it's almost free architecturally**: nothing new in the voice
pipeline — the setup assistant is just an Agent row like any other.
The "magic" is its system prompt + 5 new skills.

## Skills to ship (new file: `skills/builtin/setup.py`)

All five skills mutate **the user's draft agent**, not the
assistant itself. The draft's id is stashed on
`SkillContext.metadata["draft_agent_id"]` by `instantiate_template`
so the others can find it.

| Skill | Args | Returns |
|---|---|---|
| `list_templates(filter?)` | `category?: str, language?: str` | `[{id, name, tagline, category, language}]` |
| `recommend_template(description)` | `description: str` (free text) | `{template_id, confidence, reasoning}` (LLM-classified) |
| `instantiate_template(template_id, name)` | both `str` | `{agent_id, name, template_id}` — sets `ctx.metadata["draft_agent_id"]` |
| `update_agent_field(field, value)` | `field: str` from the allow-list, `value: str/dict/list` | `{updated: True, field, value}` |
| `publish_agent()` | (uses draft_agent_id) | `{published: True, agent_id}` |
| `describe_remaining_setup()` | (uses draft_agent_id) | `{required: [{label, why, where_to_set}]}` |

**Field allow-list for `update_agent_field`** (anything outside this
list is rejected; the LLM can't accidentally inject into channels
/ mcp_servers / status):

```python
_VOICE_EDITABLE_FIELDS = {
    "name", "description", "greeting", "system_prompt",
    "voice_id", "voice_language", "temperature", "max_tokens",
    "skills",       # list[str] of skill ids
    "voice_map",    # dict[str, str] for multilingual
}
```

## Built-in template (new entry in `api/routes/templates.py`)

```python
{
    "id": "setup-assistant",
    "name": "Setup Assistant",
    "category": "meta",
    "tagline": "Build voice agents by talking to me.",
    "icon": "🪄",
    "default": {
        "system_prompt": _SETUP_ASSISTANT_PROMPT,  # long, careful
        "greeting": "Hi — describe the agent you'd like to build, "
                    "or what your customers will use it for.",
        "voice_id": "en_male_tim_uranus_bigtts",
        "skills": [
            "list_templates", "recommend_template",
            "instantiate_template", "update_agent_field",
            "publish_agent", "describe_remaining_setup",
        ],
        "temperature": 0.3,  # lower than usual — we want consistent skill calls
        "max_tokens": 800,
    },
}
```

**Prompt design notes** (the `_SETUP_ASSISTANT_PROMPT` constant):

- Instruction to **read back every field after writing** ("OK, I
  set the greeting to ..." → "Sound right?"). Catches mishears
  before they accumulate.
- Instruction to **never ask the user for tokens / keys / URLs**
  — defer those to `describe_remaining_setup` at the end.
- Instruction to **classify before instantiating**: ask 1–2
  clarifying questions before locking the template choice. "Are
  you booking appointments, or qualifying leads?" beats jumping
  straight to receptionist for an SDR use case.
- Explicit fallback: "If the user says something you don't
  understand, say so plainly and ask them to rephrase. Don't
  guess." Voice mishears compound; "What did you say?" is fine.

## Dashboard routes

New files:

- `apps/dashboard/src/app/dashboard/agents/new/page.tsx` — chooser
  (Form / Voice) when no `mode` query param; redirects to existing
  form when `?mode=form`; renders the voice flow when `?mode=voice`.
- `apps/dashboard/src/components/setup/SetupAssistant.tsx` — the
  split-pane component: left side reuses the playground's voice
  pipeline (with text input fallback), right side polls
  `/api/v1/agents/{draft_id}` via SWR for the live preview.

Edits:

- `apps/dashboard/src/components/nav/topbar.tsx` — "New agent"
  button routes to `/dashboard/agents/new` (chooser), not the form
  directly.
- `apps/dashboard/src/app/page.tsx` (public landing) — secondary
  CTA: "🎙 Try voice setup" alongside the existing "Open dashboard"
  primary.

## Backend extras

- `/api/v1/agents/{id}` PUT should already work for partial fields
  — verify it accepts `{greeting: "..."}` without the full body.
  If not (Pydantic strict mode), add a `PATCH` route that updates
  only supplied keys.
- `SkillContext.metadata` already exists — no schema change.

## Acceptance criteria

A non-technical user can:

1. Click "🎙 Try voice setup" from the landing page.
2. Be greeted by the Setup Assistant: "Hi — describe the agent you'd like to build."
3. Say "I run a salon and want to book appointments by phone."
4. Hear the assistant recommend Receptionist, ask for the salon name, set up greeting + hours.
5. See the right-hand preview update *live* as each field lands.
6. Switch to typing mid-flow ("type 'Acme Salon and Spa' instead of saying it") and the same skill calls still run.
7. At the end, the assistant says "Three things still need your tokens: Twilio phone number, BytePlus voice activation, and MCP for HubSpot. Click those fields to fill them, then hit Publish."
8. Click Publish → the agent is live; user can immediately Test it from the playground.

## Effort

| Piece | Time |
|---|---|
| `skills/builtin/setup.py` (5 skills + allow-list + tests) | 0.5 day |
| `setup-assistant` template + prompt iteration | 0.25 day |
| `/dashboard/agents/new` chooser page | 0.5 day |
| `SetupAssistant.tsx` split-pane (voice + text + live preview) | 0.75 day |
| Topbar + landing-page CTAs | 0.25 day |
| End-to-end test pass with at least 3 template choices | 0.25 day |
| **Total** | **~2.5 days** |

## Risks & mitigations

- **Risk: LLM hallucinates a template_id that doesn't exist.**
  *Mitigation*: `instantiate_template` validates against the live
  catalogue; returns error → assistant has to retry with a real id.
- **Risk: User can't get past STT errors** ("acme" → "akami"
  every time).
  *Mitigation*: text-input fallback (the "+ text" half of hybrid)
  is the canonical escape hatch. Assistant prompt: "If your name
  is hard to spell phonetically, you can type it in the box."
- **Risk: Setup-assistant agent itself eats LLM budget.**
  *Mitigation*: temperature=0.3 + max_tokens=800 + the skill calls
  are short. Should be well under $0.05 per setup session.
- **Risk: Voice mishears template choice and instantiates the wrong
  one; user then has to start over.**
  *Mitigation*: assistant must read back its chosen template before
  calling `instantiate_template` ("I'll set you up with the
  Receptionist template — that right?"). Prompt enforces this.

## How to pick this up in a future session

1. **Read this file first.**
2. Confirm Session 9 is fully done (eval UI, pricing card,
   WeChat/Lark audio bridges, etc.) — Session 10 was explicitly
   gated on that.
3. Don't re-litigate the two locked decisions (hybrid + landing
   page first-class).
4. Don't expand scope into editing published agents or
   voice-dictating tokens — both are out of scope by design.
