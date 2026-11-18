"""7TV emote provider.

Loads the global 7TV emote set plus the watched channel's own 7TV set, caches
them in memory and refreshes them in the background. The dashboard fetches the
merged result from /api/emotes and renders chat words that match an emote name.

The channel's Twitch user ID is needed to find its 7TV set. It comes from
TWITCH_BROADCASTER_ID if that is set, and is otherwise picked up from the
`room-id` tag Twitch sends when we join the channel (see twitch_chat.py).
"""

import asyncio
import logging
import os
import re

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://7tv.io/v3"
CDN_BASE = "https://cdn.7tv.app/emote"

# 1x-4x are available. 2x stays sharp at the 28px chat height on hi-dpi screens.
# The .webp files are animated for animated emotes and a single frame otherwise.
EMOTE_SIZE = "2x"

ACTIVE_ZERO_WIDTH = 1 << 0  # flag on an emote as placed in a set
EMOTE_ZERO_WIDTH = 1 << 8   # flag on the emote itself

RETRY_SECONDS = 30

_EMOTE_ID = re.compile(r"^[A-Za-z0-9]{8,40}$")
_TWITCH_ID = re.compile(r"^[0-9]{1,20}$")


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_emotes(items, scope: str) -> dict:
    """Turn a 7TV `emotes` array into {emote name: info}."""
    parsed = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        emote_id = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if not name or not _EMOTE_ID.match(emote_id):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            data = {}
        zero_width = bool(
            _as_int(item.get("flags")) & ACTIVE_ZERO_WIDTH
            or _as_int(data.get("flags")) & EMOTE_ZERO_WIDTH
        )
        parsed[name] = {
            "id": emote_id,
            "url": f"{CDN_BASE}/{emote_id}/{EMOTE_SIZE}.webp",
            "animated": bool(data.get("animated")),
            "zero_width": zero_width,
            "scope": scope,
        }
    return parsed


class SevenTVEmotes:
    def __init__(self, channel_id: str = ""):
        self.channel_id = ""
        self.refresh_seconds = self._env_refresh_seconds()

        self.global_emotes: dict = {}
        self.channel_emotes: dict = {}
        self.global_loaded = False
        self.channel_loaded = False
        self.channel_found = False  # does the channel have a 7TV account/set?
        self.error: str | None = None

        self.merged: dict = {}
        self.version = 0  # bumped whenever the payload the dashboard sees changes
        self._signature = None

        self._wake = asyncio.Event()
        self.set_channel_id(channel_id)

    @staticmethod
    def _env_refresh_seconds() -> int:
        try:
            return max(60, int(os.getenv("EMOTE_REFRESH_SECONDS", "300")))
        except ValueError:
            return 300

    def set_channel_id(self, value) -> None:
        """Called with the channel's Twitch user ID once it is known."""
        value = str(value or "").strip()
        if not _TWITCH_ID.match(value) or value == self.channel_id:
            return
        if self.channel_id:
            log.info("Channel ID changed %s -> %s", self.channel_id, value)
        self.channel_id = value
        self.channel_emotes = {}
        self.channel_loaded = False
        self.channel_found = False
        self._rebuild()
        self._wake.set()

    def reset_channel(self) -> None:
        """Forget the current channel (the watched channel was switched)."""
        self.channel_id = ""
        self.channel_emotes = {}
        self.channel_loaded = False
        self.channel_found = False
        self._rebuild()
        self._wake.set()

    def status(self) -> str:
        if not self.global_loaded and not self.merged:
            return "error" if self.error else "loading"
        if not self.channel_id:
            return "waiting_for_channel"
        if not self.channel_loaded:
            return "error" if self.error else "loading_channel"
        return "ready"

    def payload(self) -> dict:
        return {
            "status": self.status(),
            "version": self.version,
            "channel_id": self.channel_id or None,
            "channel_found": self.channel_found,
            "counts": {
                "global": len(self.global_emotes),
                "channel": len(self.channel_emotes),
            },
            "emotes": self.merged,
        }

    def _rebuild(self) -> None:
        # Channel emotes win over global ones when the names clash.
        self.merged = {**self.global_emotes, **self.channel_emotes}
        signature = (
            self.status(),
            self.channel_found,
            tuple(sorted((n, e["id"], e["zero_width"]) for n, e in self.merged.items())),
        )
        if signature != self._signature:
            self._signature = signature
            self.version += 1

    @staticmethod
    async def _get_json(client: httpx.AsyncClient, url: str, allow_404: bool = False):
        response = await client.get(url)
        if allow_404 and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def refresh(self, client: httpx.AsyncClient) -> bool:
        """Reload both sets. A failure keeps whatever was loaded before."""
        ok = True

        try:
            data = await self._get_json(client, f"{API_BASE}/emote-sets/global")
            self.global_emotes = parse_emotes((data or {}).get("emotes"), "global")
            self.global_loaded = True
        except Exception as exc:
            ok = False
            self.error = f"global set: {exc}"
            log.warning("7TV global emotes failed: %s", exc)

        channel_id = self.channel_id
        if channel_id:
            try:
                data = await self._get_json(
                    client, f"{API_BASE}/users/twitch/{channel_id}", allow_404=True
                )
                if channel_id == self.channel_id:  # not switched while we waited
                    emote_set = (data or {}).get("emote_set") or {}
                    self.channel_emotes = parse_emotes(emote_set.get("emotes"), "channel")
                    self.channel_found = data is not None
                    self.channel_loaded = True
            except Exception as exc:
                ok = False
                self.error = f"channel set: {exc}"
                log.warning("7TV channel emotes failed: %s", exc)

        if ok:
            self.error = None
        self._rebuild()
        log.info(
            "7TV emotes: %d global, %d channel (channel id %s)",
            len(self.global_emotes), len(self.channel_emotes), channel_id or "unknown",
        )
        return ok

    async def run(self):
        headers = {"User-Agent": "AutoClip/0.1 (7TV emote loader)"}
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            while True:
                ok = await self.refresh(client)
                delay = self.refresh_seconds if ok else RETRY_SECONDS
                try:
                    # Wake early if the channel ID turns up while we wait.
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
