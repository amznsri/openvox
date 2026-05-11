"""REST client for the OpenVox API."""

from __future__ import annotations

from typing import Any

import httpx


class OpenVoxClient:
    def __init__(self, base_url: str = "http://localhost:3001", api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _r(self, method: str, path: str, **kw: Any) -> Any:
        r = self._client.request(method, f"{self.base_url}{path}", headers=self._headers, **kw)
        r.raise_for_status()
        if r.status_code == 204:
            return None
        return r.json()

    # ── Agents ─────────────────────────────────────────────────
    def list_agents(self) -> list[dict]:
        return self._r("GET", "/api/v1/agents")

    def get_agent(self, agent_id: str) -> dict:
        return self._r("GET", f"/api/v1/agents/{agent_id}")

    def create_agent(self, **fields: Any) -> dict:
        return self._r("POST", "/api/v1/agents", json=fields)

    def update_agent(self, agent_id: str, **fields: Any) -> dict:
        return self._r("PUT", f"/api/v1/agents/{agent_id}", json=fields)

    def publish_agent(self, agent_id: str) -> dict:
        return self._r("POST", f"/api/v1/agents/{agent_id}/publish")

    def delete_agent(self, agent_id: str) -> None:
        self._r("DELETE", f"/api/v1/agents/{agent_id}")

    # ── Templates ─────────────────────────────────────────────
    def list_templates(self) -> list[dict]:
        return self._r("GET", "/api/v1/templates")

    def instantiate_template(self, template_id: str, name: str | None = None) -> dict:
        return self._r("POST", f"/api/v1/templates/{template_id}/instantiate", json={"name": name})

    # ── Providers / skills ────────────────────────────────────
    def list_providers(self, type: str | None = None) -> list[dict]:
        return self._r("GET", f"/api/v1/providers{f'?type={type}' if type else ''}")

    def list_skills(self) -> list[dict]:
        return self._r("GET", "/api/v1/skills")

    def invoke_skill(self, skill_id: str, **args: Any) -> dict:
        return self._r("POST", "/api/v1/skills/invoke", json={"skill_id": skill_id, "args": args})

    # ── Sessions ──────────────────────────────────────────────
    def list_sessions(self, agent_id: str | None = None) -> list[dict]:
        return self._r("GET", f"/api/v1/sessions{f'?agent_id={agent_id}' if agent_id else ''}")

    def get_transcripts(self, session_id: str) -> list[dict]:
        return self._r("GET", f"/api/v1/sessions/{session_id}/transcripts")

    def close(self) -> None:
        self._client.close()
