"""Thin async client for the ScholarReach REST API (https://scholarreach-api.faraz-khan.xyz/docs).

Signs in with a bearer token, or with email + password (the token is fetched on first use and
refreshed once if it expires).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_API_URL = "https://scholarreach-api.faraz-khan.xyz"


class ScholarReachError(Exception):
    """An API call failed. `message` is safe to read out to the user."""

    def __init__(self, status: int, message: str, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def _message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        msg = detail.get("message") or "Request failed."
        if detail.get("missing"):
            msg += " Missing: " + ", ".join(detail["missing"]) + "."
        return msg
    if isinstance(detail, list) and detail:  # pydantic validation errors
        return "; ".join(str(e.get("msg", e)) for e in detail if isinstance(e, dict)) or "Invalid input."
    return "Request failed."


@dataclass
class ScholarReachAPI:
    base_url: str = DEFAULT_API_URL
    token: str | None = None
    email: str | None = None
    password: str | None = None
    timeout: float = 120.0  # email discovery and Gemini drafting can take a while
    transport: httpx.AsyncBaseTransport | None = None  # tests inject a fake API
    _http: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_env(cls) -> ScholarReachAPI:
        return cls(
            base_url=os.environ.get("SCHOLARREACH_API_URL", DEFAULT_API_URL).rstrip("/"),
            token=os.environ.get("SCHOLARREACH_TOKEN") or None,
            email=os.environ.get("SCHOLARREACH_EMAIL") or None,
            password=os.environ.get("SCHOLARREACH_PASSWORD") or None,
        )

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _login(self) -> None:
        if not (self.email and self.password):
            raise ScholarReachError(
                401, "Not signed in to ScholarReach. Set SCHOLARREACH_TOKEN, or SCHOLARREACH_EMAIL and SCHOLARREACH_PASSWORD."
            )
        r = await self.http.post("/api/v1/auth/login", json={"email": self.email, "password": self.password})
        if r.status_code != 200:
            raise ScholarReachError(r.status_code, "ScholarReach login failed: " + _message(_detail(r)))
        self.token = r.json()["access_token"]

    async def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
        if not self.token:
            await self._login()
        for attempt in range(2):
            try:
                r = await self.http.request(
                    method, path, json=json, params=params, headers={"Authorization": f"Bearer {self.token}"}
                )
            except httpx.TimeoutException as e:
                raise ScholarReachError(504, "ScholarReach is taking too long right now. Try again in a minute.") from e
            except httpx.HTTPError as e:
                raise ScholarReachError(502, "I couldn't reach ScholarReach. Check the connection.") from e
            if r.status_code == 401 and attempt == 0 and self.email and self.password:
                await self._login()  # token expired or password changed: sign in again once
                continue
            break
        if r.status_code >= 400:
            detail = _detail(r)
            raise ScholarReachError(r.status_code, _message(detail), detail)
        return None if r.status_code == 204 or not r.content else r.json()

    # ---- endpoints ----

    async def me(self) -> dict:
        return await self.request("GET", "/api/v1/me")

    async def profile(self) -> dict:
        return await self.request("GET", "/api/v1/profile")

    async def update_profile(self, changes: dict) -> dict:
        return await self.request("PUT", "/api/v1/profile", json=changes)

    async def search_leads(self, *, limit: int, research_interests: list[str] | None, countries: list[str] | None) -> dict:
        body: dict[str, Any] = {"limit": limit}
        if research_interests:
            body["research_interests"] = research_interests
        if countries:
            body["countries"] = countries
        return await self.request("POST", "/api/v1/leads/search", json=body)

    async def leads(self) -> list[dict]:
        return await self.request("GET", "/api/v1/leads")

    async def find_email(self, lead_id: int) -> dict:
        return await self.request("POST", f"/api/v1/leads/{lead_id}/find-email")

    async def draft(self, lead_id: int) -> dict:
        return await self.request("POST", f"/api/v1/leads/{lead_id}/draft")

    async def outreach(self, status: str | None = None) -> list[dict]:
        return await self.request("GET", "/api/v1/outreach", params={"status": status} if status else None)

    async def outreach_one(self, outreach_id: int) -> dict:
        return await self.request("GET", f"/api/v1/outreach/{outreach_id}")

    async def edit_outreach(self, outreach_id: int, changes: dict) -> dict:
        return await self.request("PUT", f"/api/v1/outreach/{outreach_id}", json=changes)

    async def outreach_action(self, outreach_id: int, action: str) -> dict:
        # action: regenerate | approve | unapprove | mark-replied
        return await self.request("POST", f"/api/v1/outreach/{outreach_id}/{action}")

    async def stats(self) -> dict:
        return await self.request("GET", "/api/v1/outreach/stats")

    async def gmail_status(self) -> dict:
        return await self.request("GET", "/api/v1/gmail/status")


def _detail(r: httpx.Response) -> Any:
    try:
        return r.json().get("detail")
    except Exception:
        return r.text[:300] or None
