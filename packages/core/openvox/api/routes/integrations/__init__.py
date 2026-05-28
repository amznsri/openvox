"""Per-provider OAuth integration routes.

One submodule per provider — Google today, future entries (Microsoft 365,
HubSpot, Slack workspace tokens) follow the same shape.

Each provider exposes:
  - GET  /api/v1/integrations/<provider>/start    — kick off browser flow
  - GET  /api/v1/integrations/<provider>/status   — list connected accounts
  - DELETE /api/v1/integrations/<provider>/<email>/disconnect — revoke + drop

The corresponding callback (e.g. ``/oauth/google/callback``) is also
defined in the submodule and mounted at the root of the FastAPI app
(no /api/v1 prefix) because the redirect URI registered in the
provider's console must match exactly.
"""
