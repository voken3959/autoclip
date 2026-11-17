"""Creates Twitch clips and tracks them until they are really published.

A clip goes through these statuses, all visible on the dashboard:

  waiting     (auto-clips only) waiting until a set time after chat's peak
  creating    we asked Twitch to make it
  processing  Twitch accepted the request and gave us an ID
  ready       the clip exists and has a public URL
  failed      Twitch refused, or the clip never appeared (see `error`)

Clip creation runs in the background so it never stalls the live dashboard.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from core.twitch_auth import AuthError, TwitchAuth

log = logging.getLogger(__name__)

API = "https://api.twitch.tv/helix"


class ClipError(Exception):
    """A problem with a clip request, worded for the person using the dashboard."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


class ClipManager:
    def __init__(
        self,
        monitor,
        auth: TwitchAuth,
        get_broadcaster_id=lambda: "",
        channel: str = "",
        http: httpx.AsyncClient | None = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 20.0,
    ):
        self.monitor = monitor
        self.auth = auth
        self.get_broadcaster_id = get_broadcaster_id
        self.channel = channel.lower()
        self._http = http
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

        self.auto_enabled = os.getenv("AUTO_CLIP_ENABLED", "false").strip().lower() == "true"
        self.duration = int(_env_float("CLIP_DURATION_SECONDS", 30, 5, 60))
        self.manual_cooldown = _env_float("CLIP_MANUAL_COOLDOWN_SECONDS", 8, 0, 600)
        self.max_in_flight = 3

        # Every clip gets this title. Twitch has no private-clip setting, so a
        # plain title is the most we can do.
        self.title = (os.getenv("CLIP_TITLE", "").strip() or self.channel or "AutoClip")[:100]
        # Auto-clips are made this long after chat's busiest moment, so the
        # clip has the moment and the reaction to it. Twitch's clip ends about
        # 5 seconds after the request and covers `duration` seconds.
        self.delay_after_peak = _env_float("CLIP_DELAY_AFTER_PEAK_SECONDS", 5, 0, 30)
        self.max_peak_wait = 45.0   # never wait longer than this for a spike to peak

        self.last_error: str | None = None
        self._looked_up_id = ""
        self._manual_at = 0.0
        self._uid = 0
        self._active: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ setup

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=15, headers={"User-Agent": "AutoClip/0.2 (clip creator)"}
            )
        return self._http

    async def aclose(self):
        for task in list(self._active):
            task.cancel()
        if self._http is not None:
            await self._http.aclose()

    def can_clip(self) -> tuple[bool, str | None]:
        problem = self.auth.problem()
        return (problem is None, problem)

    def state(self, redirect_uri: str | None = None) -> dict:
        ready, reason = self.can_clip()
        auth = self.auth.status()
        return {
            "ready": ready,
            "reason": reason,
            "connected": auth["connected"],
            "account": auth["login"],
            "needs_reconnect": auth["error"] is not None,
            "client_configured": auth["client_configured"],
            "secret_configured": auth["secret_configured"],
            "admin_configured": bool(os.getenv("ADMIN_KEY", "").strip()),
            "auto_enabled": self.auto_enabled,
            "duration": self.duration,
            "title": self.title,
            "delay_after_peak": self.delay_after_peak,
            "can_download": auth["can_download"],
            "redirect_uri": redirect_uri,
            "last_error": self.last_error,
            "in_flight": len(self._active),
        }

    # --------------------------------------------------------------- requests

    def request_clip(
        self,
        title: str | None = None,
        source: str = "manual",
        meta: dict | None = None,
        after_peak: bool = False,
    ) -> dict:
        """Start a clip. Returns its record straight away; the work continues in the background.

        With `after_peak` the clip waits until `delay_after_peak` seconds after
        chat's busiest moment of the spike that has just been detected.
        """
        ready, reason = self.can_clip()
        if not ready:
            raise ClipError(reason or "Clipping isn't set up yet.")
        if len(self._active) >= self.max_in_flight:
            raise ClipError("Clips are still being created. Try again in a few seconds.", 429)

        now = time.monotonic()
        if source == "manual":
            wait = self.manual_cooldown - (now - self._manual_at)
            if wait > 0:
                raise ClipError(f"Wait {int(wait) + 1}s before clipping again.", 429)
            self._manual_at = now

        self._uid += 1
        waiting = after_peak and self.delay_after_peak > 0
        record = {
            "uid": f"c{self._uid}",
            "title": (title or self.title).strip()[:100],
            "source": source,
            "status": "waiting" if waiting else "creating",
            "due_in": None,
            "time": datetime.now(timezone.utc).isoformat(),
            "clip_id": None,
            "url": None,
            "edit_url": None,
            "thumbnail_url": None,
            "error": None,
            **(meta or {}),
        }
        self.monitor.add_clip(record)

        # The spike began a few seconds before it was detected.
        wait_from = self.monitor.current_second - 10 if waiting else None
        task = asyncio.create_task(self._run(record, wait_from))
        self._active.add(task)
        task.add_done_callback(self._active.discard)
        return record

    # ------------------------------------------------------------- background

    async def _wait_for_peak(self, record: dict, start_tick: int):
        """Wait until `delay_after_peak` seconds after the busiest moment so far.

        If chat keeps getting busier the peak moves and so does the wait, up to
        `max_peak_wait` seconds in total.
        """
        deadline = time.monotonic() + self.max_peak_wait
        while True:
            peak_tick, _ = self.monitor.peak_since(start_tick)
            due = peak_tick + self.delay_after_peak - self.monitor.current_second
            record["due_in"] = max(0, int(round(due)))
            if due <= 0 or time.monotonic() >= deadline:
                break
            await asyncio.sleep(1)
        record["due_in"] = None

    async def _run(self, record: dict, wait_from: int | None = None):
        try:
            if wait_from is not None:
                await self._wait_for_peak(record, wait_from)
                ready, reason = self.can_clip()
                if not ready:
                    raise ClipError(reason or "Clipping isn't set up any more.")
                record["status"] = "creating"
            await self._create_and_wait(record)
            self.last_error = None
        except ClipError as exc:
            self._fail(record, str(exc))
        except AuthError as exc:
            self._fail(record, str(exc))
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            self._fail(record, "Twitch took too long to respond. Try again.")
        except httpx.HTTPError:
            self._fail(record, "Couldn't reach Twitch. Try again in a moment.")
        except Exception:
            log.exception("Clip creation crashed")
            self._fail(record, "Something went wrong while creating the clip.")

    def _fail(self, record: dict, message: str):
        record["status"] = "failed"
        record["error"] = message
        self.last_error = message
        log.warning("Clip failed: %s", message)

    def _headers(self, token: str) -> dict:
        return {"Client-Id": self.auth.client_id, "Authorization": f"Bearer {token}"}

    async def _broadcaster_id(self, token: str) -> str:
        known = (self.get_broadcaster_id() or self._looked_up_id or "").strip()
        if known:
            return known
        if self.channel:
            response = await self.http.get(
                f"{API}/users", params={"login": self.channel}, headers=self._headers(token)
            )
            if response.status_code == 200:
                data = response.json().get("data") or []
                if data:
                    self._looked_up_id = str(data[0].get("id") or "")
                    if self._looked_up_id:
                        return self._looked_up_id
        raise ClipError("Couldn't find the channel's Twitch ID yet. Wait for chat to connect, then try again.")

    @staticmethod
    def _twitch_message(response: httpx.Response) -> str:
        try:
            return str(response.json().get("message") or "")[:140]
        except ValueError:
            return ""

    async def _create_and_wait(self, record: dict):
        token = await self.auth.ensure_token(self.http)
        broadcaster = await self._broadcaster_id(token)

        params = {
            "broadcaster_id": broadcaster,
            "title": record["title"],
            "duration": self.duration,
        }

        for attempt in (1, 2):
            response = await self.http.post(f"{API}/clips", params=params, headers=self._headers(token))
            if response.status_code == 401 and attempt == 1:
                token = await self.auth.force_refresh(self.http, token)
                continue
            break

        code = response.status_code
        if code not in (200, 202):
            if code == 401:
                raise ClipError(
                    "Twitch rejected the login. Connect your Twitch account again.", 409
                )
            if code == 403:
                raise ClipError(
                    "Twitch won't let this account clip the channel. The streamer may have limited clips to followers or subscribers, or turned them off."
                )
            if code == 404:
                raise ClipError("Couldn't clip. The channel has to be live.")
            if code == 429:
                raise ClipError("Twitch is rate limiting clips. Try again in a moment.", 429)
            if code >= 500:
                raise ClipError(f"Twitch had a problem on its side (error {code}). Try again in a moment.")
            detail = self._twitch_message(response)
            raise ClipError(f"Twitch refused the clip{': ' + detail if detail else f' (error {code})'}.")

        data = (response.json().get("data") or [{}])[0]
        clip_id = data.get("id")
        if not clip_id:
            raise ClipError("Twitch didn't return a clip ID.")

        record.update(
            status="processing",
            clip_id=clip_id,
            edit_url=data.get("edit_url"),
            url=f"https://clips.twitch.tv/{clip_id}",
        )

        # Twitch makes the clip asynchronously. If it still isn't there after a
        # short wait, it was never created.
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(self.poll_interval)
            try:
                check = await self.http.get(
                    f"{API}/clips", params={"id": clip_id}, headers=self._headers(token)
                )
            except httpx.HTTPError:
                continue
            if check.status_code == 200:
                found = (check.json().get("data") or [])
                if found:
                    clip = found[0]
                    record.update(
                        status="ready",
                        url=clip.get("url") or record["url"],
                        thumbnail_url=clip.get("thumbnail_url"),
                    )
                    return

        raise ClipError(
            "Twitch didn't finish the clip. The stream may have ended, or clipping is turned off for the channel."
        )

    # --------------------------------------------------------------- saving

    def find(self, uid: str) -> dict | None:
        for clip in self.monitor.clips:
            if clip.get("uid") == uid:
                return clip
        return None

    async def download_url(self, uid: str) -> tuple[str, str]:
        """A temporary link to the clip's video file, and a file name for it.

        Twitch only hands these out to the channel owner and their editors, using
        a login that was approved with the editor:manage:clips permission.
        """
        clip = self.find(uid)
        if not clip:
            raise ClipError("That clip isn't in the list any more.", 404)
        if clip["status"] != "ready" or not clip.get("clip_id"):
            raise ClipError("The clip isn't ready yet. Try again when it says Ready.")
        problem = self.auth.problem()
        if problem:
            raise ClipError(problem)
        if not self.auth.can_download:
            raise ClipError("Connect your Twitch account again to allow saving clips.")

        token = await self.auth.ensure_token(self.http)
        broadcaster = await self._broadcaster_id(token)
        if not self.auth.user_id:
            raise ClipError("Twitch hasn't confirmed which account is connected yet. Try again in a moment.")

        params = {
            "editor_id": self.auth.user_id,
            "broadcaster_id": broadcaster,
            "clip_id": clip["clip_id"],
        }
        for attempt in (1, 2):
            response = await self.http.get(f"{API}/clips/downloads", params=params, headers=self._headers(token))
            if response.status_code == 401 and attempt == 1:
                token = await self.auth.force_refresh(self.http, token)
                continue
            break

        code = response.status_code
        if code == 401:
            raise ClipError("Twitch rejected the login. Connect your Twitch account again.")
        if code == 403:
            raise ClipError(
                f"Twitch only lets {self.channel or 'the channel owner'} and their channel editors save clips. "
                "The connected account isn't one of them."
            )
        if code == 429:
            raise ClipError("Twitch is rate limiting downloads. Try again in a moment.", 429)
        if code >= 500:
            raise ClipError(f"Twitch had a problem on its side (error {code}). Try again in a moment.")
        if code != 200:
            detail = self._twitch_message(response)
            raise ClipError(f"Twitch refused the download{': ' + detail if detail else f' (error {code})'}.")

        item = (response.json().get("data") or [{}])[0]
        url = item.get("landscape_download_url") or item.get("portrait_download_url") or ""
        if not url.startswith("https://"):
            raise ClipError("Twitch hasn't made a video file for this clip yet. Try again in a minute.")

        safe_id = "".join(ch for ch in str(clip["clip_id"]) if ch.isalnum() or ch in "-_")[:80]
        name = f"{self.channel or 'clip'}-{safe_id}.mp4"
        return url, name
