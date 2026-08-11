"""Google Workspace adapter: OAuth 2.0 + Gmail read/draft/send + Calendar read/write.

Requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (external blocker: user must create an
OAuth client in Google Cloud Console — see README). Tokens are stored AES-encrypted on
the IntegrationConnection; they never leave this module (§28, §38).
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import httpx

from backend.core.config import get_settings
from backend.core.crypto import decrypt_secret, encrypt_secret
from backend.integrations.base import IntegrationAdapter, IntegrationError

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid", "email",
]


def build_auth_url(state: str) -> str:
    settings = get_settings()
    if not settings.google_client_id:
        raise IntegrationError("GOOGLE_CLIENT_ID not configured")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        raise IntegrationError(f"google token exchange failed: {resp.status_code}")
    return resp.json()


def encrypt_tokens(tokens: dict) -> str:
    return encrypt_secret(json.dumps({
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + tokens.get("expires_in", 3600) - 60,
    }))


class GoogleAdapter(IntegrationAdapter):
    integration_type = "google"

    def capabilities(self) -> list[str]:
        return ["gmail.read", "gmail.draft", "gmail.send", "calendar.read", "calendar.write"]

    def _tokens(self) -> dict:
        if not self.connection.encrypted_credentials:
            raise IntegrationError("google not authorized")
        return json.loads(decrypt_secret(self.connection.encrypted_credentials))

    async def _access_token(self) -> str:
        tokens = self._tokens()
        if time.time() < tokens.get("expires_at", 0):
            return tokens["access_token"]
        settings = get_settings()
        if not tokens.get("refresh_token"):
            raise IntegrationError("google token expired and no refresh token")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data={
                "refresh_token": tokens["refresh_token"],
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            })
        if resp.status_code != 200:
            raise IntegrationError(f"google token refresh failed: {resp.status_code}")
        fresh = resp.json()
        tokens["access_token"] = fresh["access_token"]
        tokens["expires_at"] = time.time() + fresh.get("expires_in", 3600) - 60
        self.connection.encrypted_credentials = encrypt_secret(json.dumps(tokens))
        return tokens["access_token"]

    async def _get(self, url: str, params: dict | None = None) -> dict:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            raise IntegrationError(f"google api {resp.status_code}")
        return resp.json()

    async def _post(self, url: str, payload: dict) -> dict:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code not in (200, 201):
            raise IntegrationError(f"google api {resp.status_code}")
        return resp.json()

    async def health_check(self) -> bool:
        try:
            await self._get(f"{GMAIL_API}/users/me/profile")
            return True
        except Exception:
            return False

    async def read(self, operation: str, params: dict) -> dict:
        if operation == "gmail.list_messages":
            query = {"maxResults": min(int(params.get("max_results", 50)), 200)}
            if params.get("q"):
                query["q"] = params["q"]
            return await self._get(f"{GMAIL_API}/users/me/messages", query)
        if operation == "gmail.get_message":
            fmt = params.get("format", "metadata")
            query: dict = {"format": fmt}
            if fmt == "metadata":
                query["metadataHeaders"] = ["From", "To", "Subject", "Date"]
            return await self._get(f"{GMAIL_API}/users/me/messages/{params['id']}", query)
        if operation == "calendar.list_events":
            query = {
                "maxResults": min(int(params.get("max_results", 100)), 500),
                "singleEvents": "true", "orderBy": "startTime",
            }
            for k in ("timeMin", "timeMax"):
                if params.get(k):
                    query[k] = params[k]
            cal = params.get("calendar_id", "primary")
            return await self._get(f"{CALENDAR_API}/calendars/{cal}/events", query)
        raise IntegrationError(f"unknown read operation: {operation}")

    async def execute(self, operation: str, params: dict) -> dict:
        if operation == "gmail.create_draft":
            import base64
            from email.mime.text import MIMEText

            msg = MIMEText(params.get("body", ""))
            msg["To"] = params["to"]
            msg["Subject"] = params.get("subject", "")
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            return await self._post(f"{GMAIL_API}/users/me/drafts", {"message": {"raw": raw}})
        if operation == "gmail.send":
            import base64
            from email.mime.text import MIMEText

            msg = MIMEText(params.get("body", ""))
            msg["To"] = params["to"]
            msg["Subject"] = params.get("subject", "")
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            return await self._post(f"{GMAIL_API}/users/me/messages/send", {"raw": raw})
        if operation == "calendar.create_event":
            cal = params.get("calendar_id", "primary")
            return await self._post(f"{CALENDAR_API}/calendars/{cal}/events", {
                "summary": params.get("summary", ""),
                "description": params.get("description", ""),
                "start": params["start"],
                "end": params["end"],
                "attendees": params.get("attendees", []),
            })
        raise IntegrationError(f"unknown execute operation: {operation}")
