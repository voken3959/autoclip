"""Twitch user-token handling for clip creation.

Creating a clip needs a *user* access token with the `clips:edit` scope. Those
tokens expire after a few hours, so this module:

  * runs the "Authorization code" sign-in (state-protected) so you can connect
    your Twitch account from the dashboard,
  * refreshes the access token automatically before it expires,
  * validates the token regularly (Twitch asks apps to do this hourly),
  * keeps the tokens in memory and, best-effort, in a private file so a restart
    does not lose them.

Where the tokens can come from, in order:
  1. the token file (TOKEN_STORE_PATH, default .autoclip_tokens.json)
  2. TWITCH_CLIP_REFRESH_TOKEN  - survives hosts with a wiped disk (e.g. Render)
  3. TWITCH_CLIP_TOKEN          - a plain access token; works until it expires

Tokens are never logged and never sent to the dashboard.
"""

import asyncio
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
REVOKE_URL = "https://id.twitch.tv/oauth2/revoke"

SCOPE = "clips:edit"
REFRESH_MARGIN = 120      # refresh when fewer than this many seconds are left
STATE_TTL = 600           # a sign-in must be completed within 10 minutes
CHECK_EVERY = 30 * 60     # background validation interval

MSG_REAUTH = "Twitch no longer accepts the saved login. Connect your Twitch account again."
MSG_SCOPE = "The connected Twitch account is missing the clips:edit permission. Connect again and approve it."
MSG_NOT_CONNECTED = "Connect your Twitch account to enable clipping."
MSG_NO_APP = "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET on the server, then connect your Twitch account."


class AuthError(Exception):
    """An error whose message is safe to show to the user."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def _clean(token: str) -> str:
    return (token or "").strip().removeprefix("oauth:").strip()


class TwitchAuth:
    def __init__(self, store_path: str | None = None):
        self.client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
        self.store_path = store_path or os.getenv("TOKEN_STORE_PATH", ".autoclip_tokens.json")

        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0       # epoch seconds, 0 = unknown
        self.login: str | None = None
        self.user_id: str | None = None
        self.scopes: list[str] = []
        self.error: str | None = None   # None | "reauth" | "scope"

        self._seed_refresh = ""
        self._states: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._load()

    # ------------------------------------------------------------------ state

    @property
    def has_app(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def has_token(self) -> bool:
        return bool(self.access_token or self.refresh_token)

    @property
    def connected(self) -> bool:
        return self.has_token and self.error is None

    def status(self) -> dict:
        return {
            "client_configured": bool(self.client_id),
            "secret_configured": bool(self.client_secret),
            "connected": self.connected,
            "login": self.login,
            "error": self.error,
        }

    def problem(self) -> str | None:
        """Why clipping can't work right now (None if the token side is fine)."""
        if self.error == "scope":
            return MSG_SCOPE
        if self.error == "reauth":
            return MSG_REAUTH
        if not self.has_token:
            return MSG_NOT_CONNECTED if self.has_app else MSG_NO_APP
        if not self.client_id:
            return "Set TWITCH_CLIENT_ID on the server."
        return None

    # ---------------------------------------------------------------- storage

    def _load(self):
        data = {}
        try:
            with open(self.store_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("Could not read the Twitch token file; ignoring it.")

        if isinstance(data, dict):
            self.access_token = _clean(data.get("access_token", ""))
            self.refresh_token = _clean(data.get("refresh_token", ""))
            try:
                self.expires_at = float(data.get("expires_at") or 0)
            except (TypeError, ValueError):
                self.expires_at = 0.0
            self.login = data.get("login") or None

        self._seed_refresh = _clean(os.getenv("TWITCH_CLIP_REFRESH_TOKEN", ""))
        if not self.refresh_token:
            self.refresh_token = self._seed_refresh
        if not self.access_token:
            self.access_token = _clean(os.getenv("TWITCH_CLIP_TOKEN", ""))

    def _save(self):
        payload = json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "login": self.login,
        })
        tmp = f"{self.store_path}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.store_path)
        except OSError:
            log.warning(
                "Could not save the Twitch login to disk. It will be lost on restart "
                "unless TWITCH_CLIP_REFRESH_TOKEN is set."
            )

    def _forget_file(self):
        try:
            os.remove(self.store_path)
        except OSError:
            pass

    def _clear(self):
        self.access_token = self.refresh_token = ""
        self._seed_refresh = ""
        self.expires_at = 0.0
        self.login = self.user_id = None
        self.scopes = []
        self.error = None
        self._forget_file()

    # ----------------------------------------------------------------- tokens

    def _expiring(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at - REFRESH_MARGIN

    def _apply_tokens(self, data: dict):
        self.access_token = _clean(data.get("access_token", ""))
        new_refresh = _clean(data.get("refresh_token", ""))
        if new_refresh:
            self.refresh_token = new_refresh
        try:
            expires_in = int(data.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        self.expires_at = time.time() + expires_in if expires_in else 0.0
        self.error = None

    async def _refresh(self, client: httpx.AsyncClient):
        """Exchange the refresh token for a new access token (lock must be held)."""
        if not self.has_app:
            self.error = "reauth"
            raise AuthError(MSG_REAUTH, "reauth")

        candidates = [t for t in (self.refresh_token, self._seed_refresh) if t]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            self.error = "reauth"
            raise AuthError(MSG_REAUTH, "reauth")

        for refresh_token in candidates:
            try:
                response = await client.post(TOKEN_URL, data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                })
            except httpx.HTTPError:
                raise AuthError("Couldn't reach Twitch to renew the login. Try again in a moment.", "network")

            if response.status_code == 200:
                self.refresh_token = refresh_token
                self._apply_tokens(response.json())
                self._save()
                return
            if response.status_code not in (400, 401):
                raise AuthError(f"Twitch couldn't renew the login (error {response.status_code}).", "twitch")

        self.access_token = ""
        self.error = "reauth"
        raise AuthError(MSG_REAUTH, "reauth")

    async def ensure_token(self, client: httpx.AsyncClient) -> str:
        """A usable access token, refreshing it first if it is about to expire."""
        async with self._lock:
            if self.error == "reauth":
                raise AuthError(MSG_REAUTH, "reauth")
            if self.error == "scope":
                raise AuthError(MSG_SCOPE, "scope")
            if self.access_token and not self._expiring():
                return self.access_token
            if self.refresh_token:
                await self._refresh(client)
                return self.access_token
            if self.access_token:
                # A plain token we can't renew; let Twitch decide if it still works.
                return self.access_token
            raise AuthError(MSG_NOT_CONNECTED if self.has_app else MSG_NO_APP, "not_connected")

    async def force_refresh(self, client: httpx.AsyncClient, stale: str) -> str:
        """Called after Twitch answered 401 for `stale`."""
        async with self._lock:
            if self.access_token and self.access_token != stale:
                return self.access_token  # someone else already refreshed
            if not self.refresh_token:
                self.error = "reauth"
                raise AuthError(MSG_REAUTH, "reauth")
            await self._refresh(client)
            return self.access_token

    async def validate(self, client: httpx.AsyncClient) -> bool:
        """Ask Twitch who the token belongs to. Refreshes once if it is rejected."""
        if not self.has_token:
            return False
        try:
            token = await self.ensure_token(client)
        except AuthError:
            return False

        for attempt in (1, 2):
            response = await client.get(VALIDATE_URL, headers={"Authorization": f"OAuth {token}"})
            if response.status_code == 200:
                data = response.json()
                self.login = data.get("login") or self.login
                self.user_id = data.get("user_id") or self.user_id
                self.scopes = list(data.get("scopes") or [])
                if not self.client_id and data.get("client_id"):
                    self.client_id = data["client_id"]
                try:
                    expires_in = int(data.get("expires_in") or 0)
                except (TypeError, ValueError):
                    expires_in = 0
                if expires_in:
                    self.expires_at = time.time() + expires_in
                self.error = None if SCOPE in self.scopes else "scope"
                if self.error is None:
                    self._save()
                return self.error is None
            if response.status_code == 401 and attempt == 1:
                try:
                    token = await self.force_refresh(client, token)
                except AuthError:
                    return False
                continue
            break

        if response.status_code == 401:
            self.error = "reauth"
        return False

    # ---------------------------------------------------------- sign-in flow

    def begin_authorization(self, redirect_uri: str) -> str:
        if not self.has_app:
            raise AuthError(MSG_NO_APP, "config")
        now = time.time()
        self._states = {s: exp for s, exp in self._states.items() if exp > now}
        state = secrets.token_urlsafe(24)
        self._states[state] = now + STATE_TTL
        return f"{AUTHORIZE_URL}?" + urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "force_verify": "true",   # always let the person pick the account
        })

    def consume_state(self, state: str) -> bool:
        expires = self._states.pop(state or "", 0)
        return expires > time.time()

    async def exchange_code(self, client: httpx.AsyncClient, code: str, redirect_uri: str):
        if not self.has_app:
            raise AuthError(MSG_NO_APP, "config")
        try:
            response = await client.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            })
        except httpx.HTTPError:
            raise AuthError("Couldn't reach Twitch to finish signing in.", "exchange")
        if response.status_code != 200:
            raise AuthError("Twitch rejected the sign-in. Check the redirect URL in your Twitch app.", "exchange")

        async with self._lock:
            self._apply_tokens(response.json())
        self.login = self.user_id = None

        ok = await self.validate(client)
        if not ok:
            code_name = "scope" if self.error == "scope" else "exchange"
            self._clear()
            raise AuthError(MSG_SCOPE if code_name == "scope" else "Twitch didn't confirm the login.", code_name)
        self._save()

    async def disconnect(self, client: httpx.AsyncClient):
        token = self.access_token
        if token and self.client_id:
            try:
                await client.post(REVOKE_URL, data={"client_id": self.client_id, "token": token})
            except httpx.HTTPError:
                pass
        self._clear()

    # -------------------------------------------------------------- background

    async def run(self, client: httpx.AsyncClient):
        """Validate on start, then every 30 minutes (which also renews the token)."""
        while True:
            try:
                if self.has_token:
                    await self.validate(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Twitch token check failed: %s", exc.__class__.__name__)
            await asyncio.sleep(CHECK_EVERY)
