# Extending OpenVox

Three things you can extend without touching the core:

1. **Skills** — tools the agent can call mid-conversation.
2. **Providers** — new STT/TTS/LLM/RTC backends.
3. **Templates** — pre-configured agent blueprints.

## Skills

A skill is an `async` Python class with a JSON-schema parameter spec.

### Built-ins

Built-in skills live in `packages/core/openvox/skills/builtin/`. Each module
exports a `SKILLS = [Cls1, Cls2, ...]` list; they're auto-loaded at startup.

### Local skills

Drop a `.py` file in `~/.openvox/skills/` and OpenVox will pick it up on the
next start.

```python
# ~/.openvox/skills/get_weather.py
from openvox.skills import BaseSkill, SkillContext

class GetWeather(BaseSkill):
    id = "get_weather"
    display_name = "Get current weather"
    description = "Look up the current weather for a city."
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string", "enum": ["c", "f"], "default": "c"},
        },
        "required": ["city"],
    }

    async def run(self, args, ctx: SkillContext):
        # ...your implementation...
        return {"city": args["city"], "temp": 21, "unit": "c"}
```

Or use the decorator form for one-shot helpers:

```python
from openvox.skills import skill

@skill(
    id="reverse",
    description="Reverse a string.",
    parameters={"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]},
)
async def Reverse(args, ctx):
    return {"out": args["s"][::-1]}
```

### Pip-installable skills

Ship a Python package and register via the `openvox.skills` entry-point group:

```toml
# pyproject.toml
[project.entry-points."openvox.skills"]
my_skill = "my_pkg:MySkill"
```

Install in the same virtualenv as the core service and it's auto-discovered.

## Providers

A provider implements one of the abstract base classes from
`openvox.providers.base`:

```python
from openvox.providers.base import LLMProvider, LLMConfig, LLMMessage, LLMResponseChunk
from collections.abc import AsyncIterator

class MyLLM(LLMProvider):
    id = "myllm"
    display_name = "My LLM"

    def is_available(self) -> bool:
        return True  # check your credentials

    async def chat_stream(
        self, messages: list[LLMMessage], config: LLMConfig
    ) -> AsyncIterator[LLMResponseChunk]:
        # call your API, yield chunks
        ...
```

Register either in `packages/core/openvox/providers/bootstrap.py` (built-in)
or via the `openvox.providers` entry-point (third-party).

The same pattern applies to `STTProvider`, `TTSProvider`, `RTCProvider`.

## Templates

The catalogue lives in
`packages/core/openvox/api/routes/templates.py`. Add a dict to `TEMPLATES`:

```python
{
  "id": "my-template",
  "name": "My Custom Template",
  "tagline": "...",
  "category": "Custom",
  "icon": "Sparkles",      # any lucide-react icon name
  "use_cases": ["...", "..."],
  "default": {
    "name": "My Default Agent",
    "system_prompt": "...",
    "greeting": "...",
    "skills": ["my_skill"],
    "voice_id": "en_male_tim_uranus_bigtts",
  },
}
```

Templates immediately appear in the dashboard at `/dashboard/templates`.

## Channels (telephony)

OpenVox ships first-class support for **Telegram**, **WhatsApp Business**,
**WeChat Work (WeCom)**, **Lark**, and **Twilio**. Telegram supports
both polling mode (default — no public URL needed) and webhook mode
(production — needs public HTTPS URL).

### WeChat — Work vs Personal

OpenVox supports **WeChat Work (企业微信 / WeCom)** via its official
API. We deliberately do **not** bundle a WeChat **Personal** account
adapter, despite open-source libraries (Wechaty + PadLocal /
Wechat4U / itchat) being available. Reasons:

1. **Account ban risk.** As of 2026, WeChat actively cracks down on
   personal accounts that use unofficial APIs. Bans are commonly
   permanent and there's no appeal path. For Chinese users in
   particular, personal WeChat is tied to identity, payments, and
   social graph — the cost of losing it is severe.
2. **No stable free option.** The only reliable WeChat personal
   protocol (PadLocal) is a paid commercial service (~$25/month),
   which conflicts with OpenVox's local-first / no-recurring-cost
   ethos. Free libraries (Wechat4U, itchat) break monthly as WeChat
   changes their unofficial protocol.
3. **WeChat Work covers the legitimate bot use case.** If you want
   a customer-facing WeChat bot for your business, the official
   WeChat Work API (already supported in OpenVox) is the right
   path: free, official, sanctioned, and won't get the operator's
   account banned.

If you specifically want personal-account WeChat automation, you
can self-integrate Wechaty + PadLocal as a custom channel adapter —
follow the WhatsApp Personal pattern in
`packages/core/openvox/telephony/whatsapp_personal.py` as a reference
once that lands. We don't ship it as a first-class option to keep
new users out of the ban-risk path by default.
