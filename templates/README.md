# Templates

Pre-built voice agent blueprints. Each one is a fully-configured agent
(prompt + skills + voice) that ships with OpenVox and is one click away
in the dashboard at `/dashboard/templates`.

| ID                  | Name                            | Skills                                                     |
| ------------------- | ------------------------------- | ---------------------------------------------------------- |
| `ecommerce-support` | E-commerce Customer Support     | `lookup_order`, `start_return`, `check_stock`, `get_time`  |
| `education-tutor`   | Science & Math Tutor            | `calculator`, `explain_concept`, `web_search`              |
| `stock-analyst`     | Stock Market Analyst            | `get_quote`, `technical_indicators`, `get_time`            |
| `voice-analyzer`    | Voice Recording Analyzer        | `sentiment_analyze`, `profanity_check`                     |

The actual implementations live in
`packages/core/openvox/api/routes/templates.py` (catalogue) and
`packages/core/openvox/skills/builtin/` (skill code).

To add your own template:

1. Drop a new entry into `TEMPLATES` in `templates.py`.
2. (Optional) Ship its skills as a Python package with the
   `openvox.skills` entry-point, or put files in
   `~/.openvox/skills/`.
