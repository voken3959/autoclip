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
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from core.twitch_auth import AuthError, TwitchAuth

log = logging.getLogger(__name__)

API = "https://api.twitch.tv/helix"

# Twitch's own download link (Get Clips Download) only works for the channel
# owner and their editors. For everyone else, saving falls back to what the
# Twitch website itself does to play a clip: ask its public GraphQL server for
# the clip's video files. This is not an official API, so Twitch can change it.
# These are the web player's public client ID and saved query (the same ones the
# yt-dlp downloader uses).
WEB_GQL_URL = "https://gql.twitch.tv/gql"
WEB_CLIENT_ID = "ue6666qo983tsx6so1t0vnawi233wa"
WEB_CLIP_QUERY = "2db6a3b20eabf510bd3cf465ae2408834b59eb6b8af89ca73ab1486cacecfb63"
_CLIP_SLUG = re.compile(r"^[A-Za-z0-9_-]{4,100}$")


def _direct_video_url(page_url: str) -> tuple[str, dict]:
    """Last resort: finds a clip's video file with yt-dlp (if it is installed).

    yt-dlp is kept up to date by its authors when Twitch changes its site, so a
    redeploy (which installs the newest version) often fixes a broken Save.
    """
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "best[ext=mp4]/best",
        "socket_timeout": 15,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(page_url, download=False)

    url = info.get("url")
    headers = info.get("http_headers") or {}
    if not url:
        files = [
            f for f in (info.get("formats") or [])
            if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none"
        ]
        if files:
            best = max(files, key=lambda f: f.get("height") or 0)
            url, headers = best["url"], best.get("http_headers") or headers
    if not url or not str(url).startswith("https://"):
        raise ValueError("no video file found")
    return str(url), dict(headers)


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

        # Save: when Twitch won't give this account a download link, find the
        # video file the way the Twitch website does. Set to false to turn off.
        self.fallback_download = os.getenv("CLIP_FALLBACK_DOWNLOAD", "true").strip().lower() != "false"
        self._official_denied: set[tuple[str, str]] = set()

        self.last_error: str | None = None
        self._looked_up_id = ""
        self._manual_at = 0.0
        self._uid = 0
        self._active: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ setup

    def set_channel(self, channel: str) -> None:
        """The watched channel was switched."""
        self.channel = channel.lower()
        self._looked_up_id = ""
        self.last_error = None
        self.title = (os.getenv("CLIP_TITLE", "").strip() or self.channel or "AutoClip")[:100]

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

    def _clip_filename(self, clip: dict) -> str:
        safe_id = "".join(ch for ch in str(clip["clip_id"]) if ch.isalnum() or ch in "-_")[:80]
        return f"{self.channel or 'clip'}-{safe_id}.mp4"

    async def download_url(self, uid: str) -> tuple[str, str, dict]:
        """A link to the clip's video file, a file name for it, and extra request headers.

        Twitch's own download link is tried first, but it only exists for the
        channel owner and their editors. For anyone else (or if it fails) the
        file is found the way the Twitch website does it, so Save works for any
        clip. CLIP_FALLBACK_DOWNLOAD=false turns that backup off.
        """
        clip = self.find(uid)
        if not clip:
            raise ClipError("That clip isn't in the list any more.", 404)
        if clip["status"] != "ready" or not clip.get("clip_id"):
            raise ClipError("The clip isn't ready yet. Try again when it says Ready.")
        name = self._clip_filename(clip)

        official = ""
        try:
            return await self._official_download(clip), name, {}
        except (ClipError, AuthError) as exc:
            official = str(exc)
        except httpx.HTTPError:
            official = "Couldn't reach Twitch."

        if not self.fallback_download:
            raise ClipError(official or "Twitch wouldn't give a download link for this clip.")

        problems = []
        for label, finder in (("website", self._web_download), ("yt-dlp", self._ytdlp_download)):
            try:
                url, headers = await finder(clip)
                log.info("Clip %s: video file found through %s", clip["clip_id"], label)
                return url, name, headers
            except ClipError as exc:
                problems.append(str(exc))

        raise ClipError(
            "Couldn't get the video file. " + " ".join(dict.fromkeys(problems))
            + " The clip is still on Twitch, so you can open it with the link and download it there."
        )

    async def _web_download(self, clip: dict) -> tuple[str, dict]:
        slug = str(clip.get("clip_id") or "")
        if not _CLIP_SLUG.match(slug):
            raise ClipError("This clip has an unusual ID.")

        operations = [{
            "operationName": "ShareClipRenderStatus",
            "variables": {"slug": slug},
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": WEB_CLIP_QUERY}},
        }]
        try:
            response = await self.http.post(
                WEB_GQL_URL,
                content=json.dumps(operations),
                headers={"Client-ID": WEB_CLIENT_ID, "Content-Type": "text/plain;charset=UTF-8"},
            )
        except httpx.HTTPError as exc:
            raise ClipError(f"Couldn't reach Twitch's website servers ({exc.__class__.__name__}).")
        if response.status_code != 200:
            raise ClipError(
                f"Twitch's website servers answered with error {response.status_code}"
                f" ({response.text[:120].strip() or 'no details'})."
            )

        try:
            result = response.json()[0]
            clip_data = (result.get("data") or {}).get("clip")
        except (ValueError, IndexError, KeyError, AttributeError, TypeError):
            raise ClipError("Twitch's website servers sent something unexpected.")
        if not clip_data:
            errors = result.get("errors") or []
            if errors:
                detail = str((errors[0] or {}).get("message") or "")[:120]
                raise ClipError(f"Twitch's website servers refused the request ({detail or 'no details'}).")
            raise ClipError("Twitch doesn't have this clip any more.")

        access = clip_data.get("playbackAccessToken") or {}
        signature, token = access.get("signature"), access.get("value")
        assets = [a for a in (clip_data.get("assets") or []) if isinstance(a, dict)]
        qualities = (assets[0].get("videoQualities") if assets else None) or clip_data.get("videoQualities") or []
        sources = [
            q for q in qualities
            if isinstance(q, dict) and str(q.get("sourceURL") or "").startswith("https://")
        ]
        if not (signature and token and sources):
            raise ClipError("Twitch hasn't published a video file for this clip yet.")

        def height(quality: dict) -> int:
            try:
                return int(float(quality.get("quality") or 0))
            except (TypeError, ValueError):
                return 0

        best = max(sources, key=height)
        parts = urlsplit(best["sourceURL"])
        query = dict(parse_qsl(parts.query))
        query.update({"sig": signature, "token": token})
        return urlunsplit(parts._replace(query=urlencode(query))), {}

    async def _ytdlp_download(self, clip: dict) -> tuple[str, dict]:
        page = clip.get("url") or f"https://clips.twitch.tv/{clip['clip_id']}"
        try:
            return await asyncio.wait_for(asyncio.to_thread(_direct_video_url, page), timeout=40)
        except asyncio.TimeoutError:
            raise ClipError("The backup downloader took too long.")
        except ImportError:
            raise ClipError("The backup downloader (yt-dlp) isn't installed on the server.")
        except Exception as exc:
            reason = str(exc).replace("\n", " ")[:140]
            log.warning("yt-dlp couldn't find the clip: %s: %s", exc.__class__.__name__, reason)
            raise ClipError(f"The backup downloader couldn't find the video file ({reason or exc.__class__.__name__}).")

    async def _official_download(self, clip: dict) -> str:
        """Twitch's own download link (channel owner and editors only)."""
        problem = self.auth.problem()
        if problem:
            raise ClipError(problem)
        if not self.auth.can_download:
            raise ClipError("Connect your Twitch account again to allow saving clips.")

        token = await self.auth.ensure_token(self.http)
        broadcaster = await self._broadcaster_id(token)
        if not self.auth.user_id:
            raise ClipError("Twitch hasn't confirmed which account is connected yet. Try again in a moment.")

        denied_key = (str(broadcaster), str(self.auth.user_id))
        if denied_key in self._official_denied:
            raise ClipError("Twitch only gives download links to the channel owner and their editors.")

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
            self._official_denied.add(denied_key)
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

        return url
