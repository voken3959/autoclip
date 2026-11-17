import asyncio
import logging
import re
import websockets

log = logging.getLogger(__name__)
IRC_URL = "wss://irc-ws.chat.twitch.tv:443"


class TwitchChatClient:
    def __init__(self, username: str, token: str, channel: str, monitor, on_room_id=None):
        self.username = username
        self.token = token.removeprefix("oauth:").strip()
        self.channel = channel.lower()
        self.monitor = monitor
        # Called with the channel's Twitch user ID (the `room-id` tag) once it is seen.
        self.on_room_id = on_room_id
        self.websocket = None

    @property
    def ready(self):
        return bool(self.username and self.token and self.channel)

    async def run(self):
        while True:
            try:
                self.monitor.set_connection("connecting")
                async with websockets.connect(IRC_URL, ping_interval=30, ping_timeout=30) as ws:
                    self.websocket = ws
                    await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    await ws.send(f"PASS oauth:{self.token}")
                    await ws.send(f"NICK {self.username.lower()}")
                    await ws.send(f"JOIN #{self.channel}")
                    self.monitor.set_connection("connected")
                    log.info("Connected to #%s Twitch chat", self.channel)

                    async for raw in ws:
                        if not await self._handle_frame(ws, raw):
                            break  # Twitch asked us to reconnect
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Twitch chat connection failed")
                self.monitor.set_connection("error", str(exc))
                await asyncio.sleep(5)

    async def _handle_frame(self, ws, raw: str) -> bool:
        """Handle one WebSocket frame. Returns False if we should reconnect.

        Twitch can pack several IRC lines into a single frame (separated by
        CRLF), so each line is handled on its own.
        """
        for line in raw.split("\r\n"):
            if not line:
                continue
            if line.startswith("PING"):
                await ws.send("PONG :tmi.twitch.tv")
            elif line.startswith(":tmi.twitch.tv RECONNECT"):
                return False
            elif " PRIVMSG #" in line:
                parsed = self._parse_privmsg(line)
                if parsed:
                    self._note_room_id(parsed.pop("room_id", ""))
                    self.monitor.add_message(parsed)
            elif " ROOMSTATE #" in line:
                self._note_room_id(self._parse_tags(line).get("room-id", ""))
        return True

    def _note_room_id(self, room_id: str):
        if room_id and self.on_room_id:
            self.on_room_id(room_id)

    @staticmethod
    def _parse_tags(line: str) -> dict:
        if not line.startswith("@"):
            return {}
        tags = {}
        for item in line[1:].split(" ", 1)[0].split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                tags[key] = value
        return tags

    def _parse_privmsg(self, raw: str):
        if " PRIVMSG #" not in raw:
            return None
        match = re.match(r"(?:@([^ ]+) )?:([^!]+)!.* PRIVMSG #[^ ]+ :(.*)$", raw)
        if not match:
            return None
        _tags_raw, username, text = match.groups()
        tags = self._parse_tags(raw)
        return {
            "username": username,
            "text": text,
            "display_name": tags.get("display-name", username),
            "emotes": tags.get("emotes", ""),
            "badges": tags.get("badges", ""),
            "room_id": tags.get("room-id", ""),
        }

    async def close(self):
        if self.websocket:
            await self.websocket.close()
