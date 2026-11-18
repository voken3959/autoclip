"""How many people are watching, sampled once a minute and kept for 24 hours.

Twitch gives the current viewer count (and nothing older), so the history is
built here: one sample per poll. Samples are saved to a small file so they
survive a restart on hosts that keep their disk. On hosts that wipe it (Render's
free plan does) the history starts again after each restart or redeploy.

Credentials, in this order:
  1. TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET (the same app used for clipping)
  2. the chat login (TWITCH_OAUTH_TOKEN), whose app is looked up from Twitch
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

API = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
DAY = 24 * 3600


class ViewerError(Exception):
    """Something went wrong asking Twitch, worded for the dashboard."""


class NeedsKeys(ViewerError):
    """No Twitch credentials are available to ask with."""


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


class ViewerTracker:
    def __init__(self, channel: str, http: httpx.AsyncClient | None = None):
        self.channel = channel.strip().lstrip("#").lower()
        self.interval = _env_int("VIEWER_POLL_SECONDS", 60, 30, 300)
        self.store_path = Path(os.getenv("VIEWER_HISTORY_PATH", ".autoclip_viewers.json"))
        self._http = http

        # (unix seconds, viewers) - viewers is None while the channel is offline
        self.samples: deque[tuple[int, int | None]] = deque()
        self.status = "starting"        # starting | ready | needs_keys | error
        self.error: str | None = None
        self.live = False
        self.count: int | None = None
        self.started_at: str | None = None
        self.version = 0                # goes up whenever a new sample is added

        self._token: str | None = None
        self._token_expires = 0.0
        self._client_id: str | None = None
        self._load()

    # ------------------------------------------------------------------ setup

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=15, headers={"User-Agent": "AutoClip/0.3 (viewer counts)"}
            )
        return self._http

    async def aclose(self):
        if self._http is not None:
            await self._http.aclose()

    def _load(self):
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            cutoff = time.time() - DAY
            for stamp, viewers in raw.get("samples", []):
                if isinstance(stamp, int) and stamp >= cutoff and (viewers is None or isinstance(viewers, int)):
                    self.samples.append((stamp, viewers))
            if self.samples:
                self.version = 1
        except (OSError, ValueError, TypeError):
            pass  # no history yet, or an unreadable file: start fresh

    def _save(self):
        try:
            tmp = self.store_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"samples": list(self.samples)}), encoding="utf-8")
            os.replace(tmp, self.store_path)
        except OSError as exc:
            log.warning("Couldn't save viewer history: %s", exc)

    # ------------------------------------------------------------ credentials

    async def _headers(self) -> dict:
        now = time.time()
        if self._token and self._client_id and now < self._token_expires - 60:
            return {"Client-Id": self._client_id, "Authorization": f"Bearer {self._token}"}

        client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
        secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip()

        if client_id and secret:
            response = await self.http.post(
                TOKEN_URL,
                data={"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"},
            )
            if response.status_code != 200:
                raise ViewerError("Twitch didn't accept TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET.")
            data = response.json()
            self._token = data["access_token"]
            self._token_expires = now + int(data.get("expires_in") or 3600)
            self._client_id = client_id
        else:
            token = os.getenv("TWITCH_OAUTH_TOKEN", "").strip()
            if token.lower().startswith("oauth:"):
                token = token[6:]
            if not token:
                raise NeedsKeys("No Twitch keys are set.")
            response = await self.http.get(VALIDATE_URL, headers={"Authorization": f"OAuth {token}"})
            if response.status_code != 200:
                raise ViewerError("Twitch didn't accept TWITCH_OAUTH_TOKEN. It may have expired.")
            data = response.json()
            if not data.get("client_id"):
                raise ViewerError("Twitch didn't say which app TWITCH_OAUTH_TOKEN belongs to.")
            self._token = token
            self._client_id = data["client_id"]
            self._token_expires = now + (int(data.get("expires_in") or 0) or 3600)

        return {"Client-Id": self._client_id, "Authorization": f"Bearer {self._token}"}

    # ---------------------------------------------------------------- polling

    async def poll(self):
        headers = await self._headers()
        response = await self.http.get(
            f"{API}/streams", params={"user_login": self.channel}, headers=headers
        )

        code = response.status_code
        if code == 401:
            self._token = None
            raise ViewerError("Twitch rejected the login. Trying again.")
        if code == 429:
            raise ViewerError("Twitch is rate limiting requests. Trying again.")
        if code >= 500:
            raise ViewerError(f"Twitch had a problem on its side (error {code}).")
        if code != 200:
            raise ViewerError(f"Twitch refused the request (error {code}).")

        streams = [s for s in (response.json().get("data") or []) if s.get("type") == "live"]
        if streams:
            stream = streams[0]
            self.live = True
            self.count = int(stream.get("viewer_count") or 0)
            self.started_at = stream.get("started_at")
        else:
            self.live = False
            self.count = None
            self.started_at = None

        self._add(int(time.time()), self.count)

    def _add(self, stamp: int, viewers: int | None):
        self.samples.append((stamp, viewers))
        cutoff = stamp - DAY
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        self.version += 1
        self._save()

    async def run(self):
        while True:
            try:
                await self.poll()
                self.status, self.error = "ready", None
            except asyncio.CancelledError:
                raise
            except NeedsKeys:
                self.status, self.error = "needs_keys", None
            except ViewerError as exc:
                self.status, self.error = "error", str(exc)
                log.warning("Viewer count: %s", exc)
            except httpx.TimeoutException:
                self.status, self.error = "error", "Twitch took too long to respond."
            except httpx.HTTPError:
                self.status, self.error = "error", "Couldn't reach Twitch."
            except Exception:
                log.exception("Viewer count crashed")
                self.status, self.error = "error", "Something went wrong asking Twitch."
            await asyncio.sleep(self.interval)

    # ---------------------------------------------------------------- output

    def summary(self) -> dict:
        """The small part sent to the dashboard every second."""
        return {
            "status": self.status,
            "live": self.live,
            "count": self.count,
            "version": self.version,
        }

    def payload(self) -> dict:
        """Everything the 24 hour graph needs."""
        return {
            **self.summary(),
            "error": self.error,
            "started_at": self.started_at,
            "interval": self.interval,
            "now": int(time.time()),
            "samples": [[stamp, viewers] for stamp, viewers in self.samples],
        }
