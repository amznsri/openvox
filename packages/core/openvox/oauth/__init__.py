"""OAuth 2.0 integrations — token store + provider-specific flow helpers.

Public surface:

  store.set_oauth_token(...)       — encrypt + persist a token bundle
  store.get_oauth_token(...)       — decrypt + return a token bundle
  store.delete_oauth_token(...)    — drop a stored integration
  store.list_oauth_integrations()  — metadata-only listing for the UI

Provider-specific helpers (added in Phase 1.2 onward):

  google.start_auth_flow(...)      — build the consent-screen URL
  google.exchange_code(...)        — turn an OAuth callback `code` into a token
  google.refresh_token(...)        — rotate an expired access_token

The token-store layer (this `oauth.store` module) is provider-
agnostic. Anything that needs a per-provider HTTP flow (Google's
token endpoint, scope-list peculiarities, etc.) lives in a
sibling submodule like `oauth.google`.
"""

from openvox.oauth.store import (
    OAuthTokenBundle,
    delete_oauth_token,
    get_oauth_token,
    list_oauth_integrations,
    set_oauth_token,
)

__all__ = [
    "OAuthTokenBundle",
    "set_oauth_token",
    "get_oauth_token",
    "delete_oauth_token",
    "list_oauth_integrations",
]
