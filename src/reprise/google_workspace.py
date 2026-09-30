"""Live Google OAuth and REST adapters. No simulated success responses."""

import asyncio
import base64
import json
import os
import tempfile
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from .config import ROOT

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GoogleAPIError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class WorkspaceConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8844/google/callback"
    token_file: Path = ROOT / ".google-workspace-token.json"
    calendar_id: str = "primary"
    task_list_id: str = "@default"
    shopping_list_id: str = ""
    time_zone: str = "Asia/Kolkata"

    @classmethod
    def from_env(cls):
        return cls(
            client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
            client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", cls.redirect_uri),
            token_file=Path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE") or cls.token_file),
            calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
            task_list_id=os.getenv("GOOGLE_TASK_LIST_ID", "@default"),
            shopping_list_id=os.getenv("GOOGLE_SHOPPING_LIST_ID", ""),
            time_zone=os.getenv("REPRISE_TIME_ZONE", "Asia/Kolkata"),
        )

    @property
    def configured(self):
        return bool(
            self.client_id
            and self.client_secret
            and not self.client_id.startswith("YOUR_")
            and not self.client_secret.startswith("YOUR_")
        )


class GoogleWorkspace:
    def __init__(self, config=None, client=None):
        self.config = config or WorkspaceConfig.from_env()
        self.client = client or httpx.AsyncClient(timeout=15)
        self.owns_client = client is None
        self._token_lock = asyncio.Lock()

    def tokens(self):
        try:
            return json.loads(self.config.token_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_tokens(self, values):
        path = self.config.token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".google-token-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(values, stream)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def status(self):
        return {
            "configured": self.config.configured,
            "connected": self.config.configured and bool(self.tokens().get("refresh_token")),
            "mode": "live",
            "time_zone": self.config.time_zone,
        }

    def authorization_url(self, state, challenge):
        if not self.config.configured:
            raise GoogleAPIError(
                "Fill GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first."
            )
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    async def exchange_code(self, code, verifier):
        values = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": verifier,
            }
        )
        if not values.get("refresh_token"):
            raise GoogleAPIError("Google did not grant offline access. Connect again with consent.")
        values["expires_at"] = time.time() + values.get("expires_in", 3600)
        self.save_tokens(values)

    async def _token_request(self, data):
        if not self.config.configured:
            raise GoogleAPIError("Google OAuth placeholders have not been filled.")
        try:
            response = await self.client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    **data,
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                },
            )
        except httpx.HTTPError:
            raise GoogleAPIError(
                "Google authorization connection failed; no action was sent."
            ) from None
        if response.status_code >= 400:
            raise GoogleAPIError(
                "Google authorization was rejected. Reconnect your Google account."
            )
        return response.json()

    async def access_token(self):
        async with self._token_lock:
            tokens = self.tokens()
            if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 60:
                return tokens["access_token"]
            if not tokens.get("refresh_token"):
                raise GoogleAPIError("Connect your Google account before executing this plan.")
            refreshed = await self._token_request(
                {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
            )
            tokens.update(refreshed)
            tokens["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
            self.save_tokens(tokens)
            return tokens["access_token"]

    async def request(self, method, url, *, body=None, params=None):
        token = await self.access_token()
        try:
            response = await self.client.request(
                method, url, json=body, params=params, headers={"Authorization": "Bearer " + token}
            )
        except httpx.HTTPError:
            raise GoogleAPIError(
                "Google connection failed; check the action in Google before retrying.",
                uncertain=method != "GET",
            ) from None
        if response.status_code >= 400:
            try:
                reason = response.json().get("error", {}).get("status", "")
            except (ValueError, AttributeError):
                reason = ""
            raise GoogleAPIError(
                f"Google API rejected the action (HTTP {response.status_code} {reason}).",
                uncertain=method != "GET"
                and (response.status_code >= 500 or response.status_code == 408),
            )
        return response.json()

    async def create_event(self, action, operation_id):
        calendar = quote(self.config.calendar_id, safe="")
        return await self.request(
            "POST",
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar}/events",
            params={"sendUpdates": "none"},
            body={
                "id": operation_id,
                "summary": action["title"],
                "description": action.get("notes", ""),
                "start": {"dateTime": action["start"], "timeZone": self.config.time_zone},
                "end": {"dateTime": action["end"], "timeZone": self.config.time_zone},
            },
        )

    async def create_task(self, action, task_list=None):
        listing = quote(task_list or self.config.task_list_id, safe="")
        body = {"title": action["title"], "notes": action.get("notes", "")}
        if action.get("due_date"):
            body["due"] = action["due_date"] + "T00:00:00.000Z"
        return await self.request(
            "POST", f"https://tasks.googleapis.com/tasks/v1/lists/{listing}/tasks", body=body
        )

    async def create_task_list(self, title):
        return await self.request(
            "POST", "https://tasks.googleapis.com/tasks/v1/users/@me/lists", body={"title": title}
        )

    async def create_email(self, action):
        message = EmailMessage()
        message["To"] = ", ".join(action["to"])
        message["Subject"] = action["subject"]
        message.set_content(action["body"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        if action["delivery"] == "send":
            return await self.request(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                body={"raw": raw},
            )
        return await self.request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            body={"message": {"raw": raw}},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self.owns_client:
            await self.client.aclose()
