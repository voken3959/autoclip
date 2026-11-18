"""Twitch chat badges shown next to usernames in the dashboard chat.

Each IRC PRIVMSG carries a `badges` tag: a comma-separated list of
`type/version` entries (e.g. `moderator/1,subscriber/12`). This module turns
those into badge images:

  * `resolve()` maps each badge to an image URL and a title, using the Helix
    Get Global Chat Badges and Get Channel Chat Badges APIs. The watched
    channel's own badges (custom subscriber art) win over the global ones.
  * Badge images are fetched with the same *app* access token as the
    leaderboard (client credentials, TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET),
    and the lists are refreshed in the background.
  * If the client keys are not configured, or a badge isn't in the API
    response, a small static map of Twitch's static-CDN badges covers the
    most common types (broadcaster, moderator, VIP, subscriber, bits).

`resolve()` is a plain sync function: it only reads in-memory caches, so the
monitor can call it while recording a message without doing any network work.
"""

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

API = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

GLOBAL_REFRESH_SECONDS = 6 * 3600   # global badge list rarely changes
CHANNEL_REFRESH_SECONDS = 60 * 60   # channel badges (custom sub art) change
RETRY_SECONDS = 30

# Human-readable titles for the static fallback badges.
STATIC_TITLES = {
    "broadcaster": "Broadcaster",
    "moderator": "Moderator",
    "vip": "VIP",
    "subscriber": "Subscriber",
    "bits": "Bits",
}

# Fallback images on Twitch's static CDN for the most common badges, used
# when the client credentials are missing or a badge is unknown to the API.
STATIC_BADGES = {
    "broadcaster": "https://static-cdn.jtvnw.net/badges/v1/5527c58c-fb7d-422d-b71b-f309dcb85cc1/1",
    "moderator": "https://static-cdn.jtvnw.net/badges/v1/3267646d-33f0-4b17-b3df-f923a41db1d0/1",
    "vip": "https://static-cdn.jtvnw.net/badges/v1/199a0dba-58f3-494e-a7fc-1fa0a1001fb8/1",
    "subscriber": "https://static-cdn.jtvnw.net/badges/v1/5d9f2208-5dd8-11e7-8513-2ff4adfae661/1",
    "bits": "https://static-cdn.jtvnw.net/badges/v1/0d85a29e-79ad-4c63-a285-3acd2c66f2ba/1",
}


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


class BadgeStore:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        get_channel_id=lambda: "",
        http: httpx.AsyncClient | None = None,
    ):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.get_channel_id = get_channel_id
        self._http = http

        # set_id -> {version_id: {"url": ..., "title": ...}}
        self._global: dict = {}
        self._channel: dict = {}
        self._channel_id = ""
        self._loaded = False
        self.last_error: str | None = None
        self._last_global_at = 0.0
        self._last_channel_at = 0.0

        self._token = ""
        self._token_expires = 0.0
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()

    @property
    def app_ready(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=15, headers={"User-Agent": "AutoClip/0.4 (chat badges)"}
            )
        return self._http

    async def aclose(self):
        if self._http is not None:
            await self._http.aclose()

    # ------------------------------------------------------------- resolution

    def resolve(self, badges_tag: str) -> list[dict]:
        """Turn an IRC `badges` tag into badge image entries (sync, no I/O)."""
        result = []
        for item in (badges_tag or "").split(","):
            item = item.strip()
            if not item:
                continue
            type_, _, version = item.partition("/")
            type_ = type_.strip().lower()
            if not type_:
                continue

            url, title = self._lookup(type_, version.strip())
            if not url:
                continue
            result.append({
                "type": type_,
                "version": version.strip(),
                "url": url,
                "title": title or STATIC_TITLES.get(type_, type_),
            })
        return result

    def _lookup(self, type_: str, version: str) -> tuple[str, str]:
        # Channel badges first (the watched channel's own subscriber art).
        for source in (self._channel, self._global):
            versions = source.get(type_)
            if not versions:
                continue
            entry = versions.get(version) or next(iter(versions.values()), None)
            if entry and entry.get("url"):
                return entry["url"], entry.get("title") or ""
        # Fallback to the well-known static badges.
        url = STATIC_BADGES.get(type_)
        return (url, "") if url else ("", "")

    # ------------------------------------------------------------- channel

    def set_channel_id(self, value) -> None:
        """Called with the watched channel's Twitch user ID once it is known."""
        value = str(value or "").strip()
        if not value or value == self._channel_id:
            return
        self._channel_id = value
        self._channel = {}
        self._wake.set()

    def reset_channel(self) -> None:
        """Forget the current channel (the watched channel was switched)."""
        self._channel_id = ""
        self._channel = {}
        self._last_channel_at = 0.0
        self._wake.set()

    # ---------------------------------------------------------------- tokens

    async def _app_token(self) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._token_expires - 60:
                return self._token

            response = await self.http.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            })
            if response.status_code != 200:
                raise RuntimeError(f"Twitch app token request failed (error {response.status_code}).")
            data = response.json()
            self._token = str(data.get("access_token") or "")
            try:
                expires_in = int(data.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires_in = 0
            self._token_expires = time.time() + max(60, expires_in)
            return self._token

    # ----------------------------------------------------------------- fetch

    @staticmethod
    def _parse_badges(data) -> dict:
        """Turn a Helix /chat/badges response into {set_id: {version: entry}}."""
        parsed = {}
        for item in data or []:
            if not isinstance(item, dict):
                continue
            set_id = str(item.get("set_id") or "").strip()
            if not set_id:
                continue
            versions = {}
            for version in item.get("versions") or []:
                if not isinstance(version, dict):
                    continue
                version_id = str(version.get("id") or "").strip()
                url = (
                    version.get("image_url_2x")
                    or version.get("image_url_1x")
                    or ""
                )
                if not url:
                    continue
                versions[version_id or "0"] = {
                    "url": str(url),
                    "title": str(version.get("title") or ""),
                }
            if versions:
                parsed[set_id] = versions
        return parsed

    async def _fetch(self, path: str, params: dict | None = None) -> dict:
        token = await self._app_token()
        headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {token}"}
        response = await self.http.get(f"{API}{path}", params=params, headers=headers)
        if response.status_code != 200:
            message = ""
            try:
                message = str(response.json().get("message") or "")[:140]
            except ValueError:
                pass
            raise RuntimeError(
                f"Twitch badge request failed (error {response.status_code})"
                + (f": {message}" if message else "")
            )
        return self._parse_badges(response.json().get("data") or [])

    async def _refresh(self) -> bool:
        ok = True
        now = time.time()

        try:
            if self._loaded and now - self._last_global_at < GLOBAL_REFRESH_SECONDS:
                pass
            else:
                self._global = await self._fetch("/chat/badges")
                self._last_global_at = now
                self._loaded = True
                log.info("Twitch badges: %d global badge set(s) loaded", len(self._global))
        except Exception as exc:
            ok = False
            self.last_error = str(exc)[:200]
            log.warning("Twitch global badges failed: %s", exc)

        channel_id = self._channel_id or self.get_channel_id() or ""
        if channel_id:
            try:
                if now - self._last_channel_at < CHANNEL_REFRESH_SECONDS:
                    pass
                else:
                    self._channel = await self._fetch(
                        "/chat/badges", params={"broadcaster_id": channel_id}
                    )
                    self._last_channel_at = now
                    if self._channel:
                        log.info("Twitch badges: %d channel badge set(s)", len(self._channel))
            except Exception as exc:
                ok = False
                self.last_error = str(exc)[:200]
                log.warning("Twitch channel badges failed: %s", exc)

        if ok:
            self.last_error = None
        return ok

    # ------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        self._last_global_at = 0.0
        self._last_channel_at = 0.0
        while True:
            try:
                if self.app_ready:
                    await self._refresh()
                else:
                    self.last_error = (
                        "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET on the server "
                        "for chat badges."
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Badge store error: %s", exc)
                self.last_error = str(exc)[:200]

            # Wake early when the channel ID turns up; otherwise wait.
            delay = CHANNEL_REFRESH_SECONDS if self._loaded else 60
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
