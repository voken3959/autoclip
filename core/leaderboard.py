"""Top live streamers with a real chat-participation metric.

The dashboard's *Chat Contribution* page lists the biggest live streams and,
for each one, the share of its live viewers who took part in chat recently:

    chat contribution = unique people who sent a message in the last 5 minutes
                        / current live viewers

Twitch has no public API for "unique active chatters", so this module is the
separate chat-collection system the dashboard footnote refers to:

  * the stream list comes from the Twitch Helix `GET /streams` endpoint using
    an *app* access token (client credentials grant, TWITCH_CLIENT_ID and
    TWITCH_CLIENT_SECRET - the same keys clipping already uses),
  * chat itself is read over IRC, the same way the watched channel's chat is
    read in `core/twitch_chat.py`. A chat:read login may join any public
    channel, so each connection joins a handful of streamer channels at once
    and records who says something.
  * per channel we keep a rolling window (default 5 minutes) of usernames;
    `snapshot()` turns that into the numbers the API returns.

Nothing here touches clipping - the two systems share credentials but never
their state.
"""

import asyncio
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone

import httpx
import websockets

log = logging.getLogger(__name__)

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.twitch.tv/helix"

# :user!user@user.tmi.twitch.tv PRIVMSG #channel :text
# The tag block at the start is optional in the regex; the fields we need are
# the sender (before the first "!") and the channel after "PRIVMSG #".
PRIVMSG_RE = re.compile(
    r"^(?:@([^ ]+) )?:([^!]+)!\S* PRIVMSG #([^ :]+) :"
)

TOKEN_EXPIRY_DEFAULT = 5184000  # 60 days, Twitch's app-token lifetime


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _thumbnail(url: str) -> str:
    """Twitch fills {width}/{height} placeholders into thumbnail URLs."""
    return (url or "").replace("{width}", "280").replace("{height}", "158")


class LeaderboardCollector:
    def __init__(
        self,
        username: str,
        token: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient | None = None,
    ):
        self.username = (username or "").strip()
        self.token = (token or "").strip().removeprefix("oauth:").strip()
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self._http = http

        self.window_seconds = env_int("LEADERBOARD_WINDOW_SECONDS", 300)
        self.refresh_seconds = env_int("LEADERBOARD_REFRESH_SECONDS", 60)
        self.per_connection = env_int("LEADERBOARD_CHANNELS_PER_CONNECTION", 20)
        self.max_streams = env_int("LEADERBOARD_MAX_STREAMS", 100)
        # A channel only reports a number once it has been collecting for at
        # least this long (or has real messages), so a stream that was just
        # added shows a dash instead of a bogus 0%.
        self.min_sample_seconds = env_int("LEADERBOARD_MIN_SAMPLE_SECONDS", 60)
        # The page re-renders from the same numbers; serving a snapshot for a
        # few seconds keeps repeated loads fast and consistent.
        self.snapshot_cache_seconds = env_int("LEADERBOARD_SNAPSHOT_CACHE_SECONDS", 10)

        self.streams: list[dict] = []          # cached, newest refresh
        self.last_error: str | None = None

        self._activity: dict[str, deque] = {}  # login -> [(monotonic ts, user)]
        self._joined_at: dict[str, float] = {} # login -> monotonic join time
        self._connections: list[_IrcConnection] = []
        self._snapshot_cache: dict | None = None
        self._snapshot_cached_at = 0.0

        self._app_token = ""
        self._app_token_expires = 0.0
        self._token_lock = asyncio.Lock()

    # ------------------------------------------------------------------ state

    @property
    def chat_ready(self) -> bool:
        """A chat:read login is available to join channels over IRC."""
        return bool(self.username and self.token)

    @property
    def app_ready(self) -> bool:
        """Client ID/secret are configured for the Helix app token."""
        return bool(self.client_id and self.client_secret)

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=15, headers={"User-Agent": "AutoClip/0.3 (chat contribution)"}
            )
        return self._http

    async def aclose(self):
        for conn in list(self._connections):
            await conn.close()
        if self._http is not None:
            await self._http.aclose()

    # --------------------------------------------------------------- Helix

    async def _get_app_token(self) -> str:
        """A short-lived app access token, cached until it nears expiry."""
        async with self._token_lock:
            now = time.time()
            if self._app_token and now < self._app_token_expires - 60:
                return self._app_token

            response = await self.http.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            })
            if response.status_code != 200:
                raise RuntimeError(f"Twitch app token request failed (error {response.status_code}).")
            data = response.json()
            self._app_token = str(data.get("access_token") or "")
            try:
                expires_in = int(data.get("expires_in") or TOKEN_EXPIRY_DEFAULT)
            except (TypeError, ValueError):
                expires_in = TOKEN_EXPIRY_DEFAULT
            self._app_token_expires = time.time() + max(60, expires_in)
            return self._app_token

    async def _refresh_streams(self) -> None:
        token = await self._get_app_token()
        headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {token}"}
        response = await self.http.get(
            f"{API}/streams", params={"first": self.max_streams}, headers=headers
        )
        if response.status_code != 200:
            message = ""
            try:
                message = str(response.json().get("message") or "")[:140]
            except ValueError:
                pass
            raise RuntimeError(
                f"Twitch didn't return the live streams (error {response.status_code})"
                + (f": {message}" if message else "")
            )

        data = response.json().get("data") or []
        streams = []
        for item in data:
            login = str(item.get("user_login") or "").strip().lower()
            if not login:
                continue
            try:
                viewers = int(item.get("viewer_count") or 0)
            except (TypeError, ValueError):
                viewers = 0
            streams.append({
                "login": login,
                "name": str(item.get("user_name") or login),
                "game": str(item.get("game_name") or ""),
                "viewers": max(0, viewers),
                "thumbnail": _thumbnail(str(item.get("thumbnail_url") or "")),
                "url": f"https://twitch.tv/{login}",
            })

        # The endpoint's default ordering is not guaranteed to be by viewers.
        streams.sort(key=lambda s: s["viewers"], reverse=True)
        self.streams = streams[: self.max_streams]
        self.last_error = None
        self._invalidate_snapshot()
        log.info("Leaderboard: %d live streams tracked", len(self.streams))

    # --------------------------------------------------------------- IRC joins

    async def _reconcile(self) -> None:
        """Keep IRC connections joined to exactly the current stream list."""
        desired = [s["login"] for s in self.streams if s.get("login")]

        if not self.chat_ready:
            await self._close_connections()
            return

        # Channels that dropped off the list stop counting towards anything.
        wanted = set(desired)
        for login in list(self._activity):
            if login not in wanted:
                self._activity.pop(login, None)
        for login in list(self._joined_at):
            if login not in wanted:
                self._joined_at.pop(login, None)

        if not desired:
            await self._close_connections()
            return

        chunks = [
            desired[i: i + self.per_connection]
            for i in range(0, len(desired), self.per_connection)
        ]
        same = (
            len(chunks) == len(self._connections)
            and all(
                set(conn.channels) == set(chunk)
                for conn, chunk in zip(self._connections, chunks)
            )
        )
        if same:
            return

        log.info(
            "Leaderboard chat: joining %d streams over %d connection(s)",
            len(desired), len(chunks),
        )
        await self._close_connections()
        now = time.monotonic()
        for chunk in chunks:
            conn = _IrcConnection(self.username, self.token, chunk, self)
            self._connections.append(conn)
            # Optimistic so snapshot() has a join time right away; the
            # connection overwrites it once IRC accepts the JOIN.
            self.note_joined(chunk, now)
            asyncio.create_task(conn.run())
        self._invalidate_snapshot()

    async def _close_connections(self) -> None:
        for conn in list(self._connections):
            await conn.close()
        self._connections.clear()

    def note_joined(self, channels, when: float | None = None) -> None:
        """Record that we started collecting chat for these channels."""
        stamp = time.monotonic() if when is None else when
        for channel in channels:
            self._joined_at.setdefault(str(channel).lower(), stamp)

    def note_left(self, channels) -> None:
        """Stop counting a channel's chat collection time."""
        for channel in channels:
            self._joined_at.pop(str(channel).lower(), None)

    def _invalidate_snapshot(self) -> None:
        """Drop the cached snapshot so the next call rebuilds it."""
        self._snapshot_cache = None
        self._snapshot_cached_at = 0.0

    def on_connection_closed(self, conn: "_IrcConnection") -> None:
        try:
            self._connections.remove(conn)
        except ValueError:
            pass
        self.note_left(conn.channels)
        self._invalidate_snapshot()

    # ------------------------------------------------------- message window

    def note_message(self, login: str, username: str, at: float | None = None) -> None:
        """Record that `username` spoke in `login`'s channel at time `at`."""
        login = (login or "").lower().strip()
        if not login:
            return
        entry = (time.monotonic() if at is None else at, (username or "").lower())
        window = self._activity.get(login)
        if window is None:
            window = deque(maxlen=20000)
            self._activity[login] = window
        window.append(entry)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        for login in list(self._activity):
            window = self._activity[login]
            while window and window[0][0] < cutoff:
                window.popleft()
            if not window:
                self._activity.pop(login, None)

    def chatters_for(self, login: str) -> int:
        """Unique people who spoke in `login` inside the rolling window."""
        window = self._activity.get(login)
        if not window:
            return 0
        cutoff = time.monotonic() - self.window_seconds
        return len({user for ts, user in window if ts >= cutoff})

    # -------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        while True:
            try:
                if self.app_ready:
                    await self._refresh_streams()
                    await self._reconcile()
                else:
                    self.last_error = (
                        "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET on the server "
                        "to load live streams."
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Leaderboard collector error: %s", exc)
                self.last_error = str(exc)[:200]
            await asyncio.sleep(self.refresh_seconds)

    # --------------------------------------------------------------- snapshot

    def snapshot(self) -> dict:
        """What /api/leaderboard returns: streams plus the contribution stats.

        The list is ordered by chat contribution (highest first); streams we
        haven't measured yet sort last.
        """
        now = time.monotonic()
        if self._snapshot_cache is not None and (
            now - self._snapshot_cached_at < self.snapshot_cache_seconds
        ):
            return self._snapshot_cache

        self._prune()

        joined = {
            login
            for conn in self._connections
            for login in conn.channels
        }

        streams = []
        contributions = []
        for item in self.streams:
            login = item["login"]
            collecting = login in joined
            chatters = self.chatters_for(login) if collecting else 0
            # Only report a number once the channel has been collecting for
            # `min_sample_seconds` (or has real messages); otherwise it is
            # "not measured yet" and shows a dash instead of a false 0%.
            elapsed = now - self._joined_at.get(login, now)
            measured = collecting and (elapsed >= self.min_sample_seconds or chatters > 0)
            chatters = chatters if measured else None
            contribution = None
            if chatters is not None and item["viewers"] > 0:
                contribution = round(chatters / item["viewers"] * 100, 2)
            if contribution is not None:
                contributions.append(contribution)

            out = dict(item)
            out["chatters"] = chatters
            out["contribution"] = contribution
            out["collecting"] = collecting
            streams.append(out)

        # Highest contribution first; unmeasured streams last. Ties break on
        # viewers so the ranking is stable and meaningful.
        streams.sort(
            key=lambda s: (
                s["contribution"] is None,
                -(s["contribution"] or 0),
                -(s["viewers"] or 0),
            )
        )

        if self.app_ready and self.chat_ready:
            chat_status = "collecting"
        elif self.app_ready:
            chat_status = "chat_not_configured"
        else:
            chat_status = "app_not_configured"

        payload = {
            "ok": True,
            "streams": streams,
            "average_contribution": (
                round(sum(contributions) / len(contributions), 2)
                if contributions
                else None
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "chat": {
                "ready": self.chat_ready,
                "status": chat_status,
                "error": self.last_error,
                "window_seconds": self.window_seconds,
                "min_sample_seconds": self.min_sample_seconds,
                "streams_joined": len(joined),
                "streams_tracked": len(self.streams),
            },
        }

        self._snapshot_cache = payload
        self._snapshot_cached_at = now
        return payload


class _IrcConnection:
    """One IRC connection that joins a group of streamer channels."""

    def __init__(
        self,
        username: str,
        token: str,
        channels: list[str],
        collector: LeaderboardCollector,
    ):
        self.username = username
        self.token = token.removeprefix("oauth:").strip()
        self.channels = [c.lower() for c in channels]
        self.collector = collector
        self._ws = None
        self._stop = False

    async def close(self) -> None:
        """Close the socket; run() then exits without reconnecting."""
        self._stop = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def run(self) -> None:
        try:
            while not self._stop:
                try:
                    async with websockets.connect(
                        IRC_URL, ping_interval=30, ping_timeout=30
                    ) as ws:
                        self._ws = ws
                        await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                        await ws.send(f"PASS oauth:{self.token}")
                        await ws.send(f"NICK {self.username.lower()}")
                        await ws.send("JOIN " + ",".join(f"#{c}" for c in self.channels))
                        self.collector.note_joined(self.channels)
                        log.info(
                            "Leaderboard IRC: joined %d channel(s)", len(self.channels)
                        )

                        async for raw in ws:
                            if not await self._handle(ws, raw):
                                break  # Twitch asked us to reconnect
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._stop:
                        break
                    log.warning("Leaderboard IRC error: %s", exc)
                    await asyncio.sleep(5)
        finally:
            self._ws = None
            self.collector.on_connection_closed(self)

    async def _handle(self, ws, raw: str) -> bool:
        for line in raw.split("\r\n"):
            if not line:
                continue
            if line.startswith("PING"):
                await ws.send("PONG :tmi.twitch.tv")
            elif " PRIVMSG #" in line:
                match = PRIVMSG_RE.match(line)
                if match:
                    _tags, username, channel = match.groups()
                    self.collector.note_message(channel, username)
            elif line.startswith(":tmi.twitch.tv RECONNECT"):
                return False
        return True
