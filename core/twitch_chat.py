import asyncio
import logging
import re
import websockets

log = logging.getLogger(__name__)
IRC_URL = "wss://irc-ws.chat.twitch.tv:443"


class TwitchChatClient:
    def __init__(self, username: str, token: str, channel: str, monitor):
        self.username = username
        self.token = token.removeprefix("oauth:").strip()
        self.channel = channel.lower()
        self.monitor = monitor
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
                        if raw.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                            continue
                        if " RECONNECT" in raw:
                            break
                        parsed = self._parse_privmsg(raw)
                        if parsed:
                            self.monitor.add_message(parsed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Twitch chat connection failed")
                self.monitor.set_connection("error", str(exc))
                await asyncio.sleep(5)

    def _parse_privmsg(self, raw: str):
        if " PRIVMSG #" not in raw:
            return None
        match = re.match(r"(?:@([^ ]+) )?:([^!]+)!.* PRIVMSG #[^ ]+ :(.*)$", raw)
        if not match:
            return None
        tags_raw, username, text = match.groups()
        tags = {}
        if tags_raw:
            for item in tags_raw.split(";"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    tags[key] = value
        return {
            "username": username,
            "text": text,
            "display_name": tags.get("display-name", username),
            "emotes": tags.get("emotes", ""),
        }

    async def close(self):
        if self.websocket:
            await self.websocket.close()
