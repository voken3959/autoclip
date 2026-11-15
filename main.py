import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from core.monitor import Monitor
from core.twitch_chat import TwitchChatClient
from core.clip_manager import ClipManager
from core.emotes import SevenTVEmotes
from dashboard.page import PAGE

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("autoclip")

monitor = Monitor()
clip_manager = ClipManager()
# TWITCH_BROADCASTER_ID (if set) lets 7TV channel emotes load straight away;
# otherwise the channel's ID is read from Twitch chat once we have joined it.
emotes = SevenTVEmotes(channel_id=os.getenv("TWITCH_BROADCASTER_ID", ""))
chat_client = TwitchChatClient(
    username=os.getenv("TWITCH_USERNAME", "").strip(),
    token=os.getenv("TWITCH_OAUTH_TOKEN", "").strip(),
    channel=os.getenv("TWITCH_CHANNEL", "jynxzi").strip().lstrip("#"),
    monitor=monitor,
    on_room_id=emotes.set_channel_id,
)
chat_task = None
broadcast_task = None
emote_task = None
clients: set[WebSocket] = set()


def snapshot():
    """Dashboard snapshot. emotes_version tells the page when to re-fetch /api/emotes."""
    data = monitor.snapshot()
    data["emotes_version"] = emotes.version
    return data


async def broadcast_loop():
    while True:
        event = monitor.tick()
        if event:
            log.info("HYPE DETECTED: %s", event)
            result = await clip_manager.create_clip(
                title=f"AutoClip highlight - {event['multiplier']:.1f}x chat activity"
            )
            if result:
                monitor.add_clip(result)

        data = snapshot()
        for ws in list(clients):
            try:
                await ws.send_json(data)
            except Exception:
                clients.discard(ws)
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_task, broadcast_task, emote_task
    broadcast_task = asyncio.create_task(broadcast_loop())
    emote_task = asyncio.create_task(emotes.run())
    if chat_client.ready:
        chat_task = asyncio.create_task(chat_client.run())
    else:
        log.warning("Twitch chat not configured. Add credentials to .env and restart.")
    yield
    for task in (chat_task, broadcast_task, emote_task):
        if task:
            task.cancel()
    await chat_client.close()


app = FastAPI(title="AutoClip", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/", response_class=HTMLResponse)
async def home():
    return PAGE


@app.get("/api/status")
async def status():
    return snapshot()


@app.get("/api/emotes")
async def emotes_endpoint():
    # Only public 7TV emote data, so it is safe for a dashboard served from
    # another origin to read it.
    return JSONResponse(
        emotes.payload(),
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json(snapshot())
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
