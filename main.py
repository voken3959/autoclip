import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

load_dotenv()

from core.monitor import Monitor
from core.twitch_chat import TwitchChatClient
from core.twitch_auth import AuthError, TwitchAuth
from core.clip_manager import ClipError, ClipManager
from core.emotes import SevenTVEmotes
from core.leaderboard import LeaderboardCollector
from dashboard.page import PAGE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("autoclip")

CHANNEL = os.getenv("TWITCH_CHANNEL", "jynxzi").strip().lstrip("#")
CALLBACK_PATH = "/auth/twitch/callback"

monitor = Monitor()
auth = TwitchAuth()
# TWITCH_BROADCASTER_ID (if set) is used straight away; otherwise the channel's
# ID is read from Twitch chat once we have joined it (also used for 7TV emotes).
emotes = SevenTVEmotes(channel_id=os.getenv("TWITCH_BROADCASTER_ID", ""))
clip_manager = ClipManager(
    monitor,
    auth,
    get_broadcaster_id=lambda: os.getenv("TWITCH_BROADCASTER_ID", "").strip() or emotes.channel_id,
    channel=CHANNEL,
)
chat_client = TwitchChatClient(
    username=os.getenv("TWITCH_USERNAME", "").strip(),
    token=os.getenv("TWITCH_OAUTH_TOKEN", "").strip(),
    channel=CHANNEL,
    monitor=monitor,
    on_room_id=emotes.set_channel_id,
)
# Collects chat from the top live streams so the Chat Contribution page can
# show a real participation number for each of them.
leaderboard = LeaderboardCollector(
    username=chat_client.username,
    token=chat_client.token,
    client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
    client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
)
tasks: list[asyncio.Task] = []
clients: set[WebSocket] = set()


# ------------------------------------------------------------------ helpers

def public_base_url(request: Request | None = None) -> str:
    """The address people use to reach this app (needed for the Twitch redirect URL)."""
    configured = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if configured or request is None:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{host.split(',')[0].strip()}"


def redirect_uri(request: Request | None = None) -> str | None:
    base = public_base_url(request)
    return f"{base}{CALLBACK_PATH}" if base else None


def snapshot(include_chat: bool = True):
    """What the dashboard receives every second (chat is pushed separately)."""
    data = monitor.snapshot(include_chat=include_chat)
    data["emotes_version"] = emotes.version
    data["auto_clip_enabled"] = clip_manager.auto_enabled
    data["clipping"] = clip_manager.state(redirect_uri())
    return data


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


async def require_admin(request: Request):
    """Clip controls need the ADMIN_KEY (or must come from this same machine)."""
    expected = os.getenv("ADMIN_KEY", "").strip()
    if not expected:
        host = request.client.host if request.client else ""
        local = host in ("127.0.0.1", "::1", "localhost") and "x-forwarded-for" not in request.headers
        if local:
            return
        raise ApiError(403, "admin_key_not_set", "Set ADMIN_KEY on the server to use the clip controls.")

    provided = request.headers.get("x-admin-key", "")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        await asyncio.sleep(1)  # slows down guessing
        raise ApiError(401, "bad_admin_key", "That admin key isn't right.")


async def read_json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# -------------------------------------------------------------- live updates

async def broadcast(data: dict):
    async def push(ws: WebSocket):
        try:
            await asyncio.wait_for(ws.send_json(data), timeout=5)
        except Exception:
            clients.discard(ws)

    if clients:
        await asyncio.gather(*(push(ws) for ws in list(clients)))


def push_chat(message: dict):
    """Sends one chat message to every dashboard right away."""
    if not clients:
        return
    try:
        asyncio.get_running_loop().create_task(broadcast({"type": "chat", "message": message}))
    except RuntimeError:
        pass  # no event loop running (shutdown)


monitor.on_chat = push_chat


async def broadcast_loop():
    while True:
        try:
            event = monitor.tick()
            if event:
                log.info("HYPE DETECTED: %s", event)
                if clip_manager.auto_enabled:
                    try:
                        clip_manager.request_clip(
                            source="auto",
                            meta={"event_id": event["id"], "multiplier": event["multiplier"]},
                            after_peak=True,
                        )
                    except ClipError as exc:
                        clip_manager.last_error = f"Auto-clip skipped: {exc}"
                        log.warning("Auto-clip skipped: %s", exc)

            if clients:
                await broadcast(snapshot(include_chat=False))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Update loop error")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks.append(asyncio.create_task(broadcast_loop()))
    tasks.append(asyncio.create_task(emotes.run()))
    tasks.append(asyncio.create_task(auth.run(clip_manager.http)))
    tasks.append(asyncio.create_task(leaderboard.run()))
    if chat_client.ready:
        tasks.append(asyncio.create_task(chat_client.run()))
    else:
        log.warning("Twitch chat not configured. Add credentials to .env and restart.")
    yield
    for task in tasks:
        task.cancel()
    tasks.clear()
    await chat_client.close()
    await leaderboard.aclose()
    await clip_manager.aclose()


app = FastAPI(title="AutoClip", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)

FONT_DIR = Path(__file__).parent / "dashboard" / "fonts"
if FONT_DIR.is_dir():
    app.mount("/fonts", StaticFiles(directory=FONT_DIR), name="fonts")


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        {"ok": False, "code": exc.code, "error": exc.message},
        status_code=exc.status,
        headers={"Cache-Control": "no-store"},
    )


# ------------------------------------------------------------------- public

@app.api_route("/ping", methods=["GET", "HEAD"], response_class=PlainTextResponse)
async def ping():
    return "OK"


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-cache"})


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


@app.get("/api/leaderboard")
async def leaderboard_endpoint():
    """Top live streams plus each one's measured chat contribution.

    Needs TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET for the stream list; the
    contribution numbers also need the chat:read login to be configured.
    """
    if not leaderboard.app_ready:
        raise ApiError(
            409,
            "leaderboard_unavailable",
            "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET on the server to load live streams.",
        )
    return leaderboard.snapshot()


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
    finally:
        clients.discard(ws)


# --------------------------------------------------------------- clip controls

@app.post("/api/admin/check")
async def admin_check(request: Request):
    await require_admin(request)
    return {"ok": True}


@app.post("/api/auto-clip")
async def set_auto_clip(request: Request):
    await require_admin(request)
    body = await read_json(request)
    enabled = bool(body.get("enabled"))
    if enabled:
        ready, reason = clip_manager.can_clip()
        if not ready:
            raise ApiError(409, "clip_unavailable", reason or "Clipping isn't set up yet.")
    clip_manager.auto_enabled = enabled
    return {"ok": True, "auto_enabled": enabled}


@app.post("/api/clip")
async def make_clip(request: Request):
    """Manual Clip now: starts a clip straight away and returns its record."""
    await require_admin(request)
    try:
        clip = clip_manager.request_clip(source="manual")
    except ClipError as exc:
        raise ApiError(exc.status, "clip_failed", str(exc))
    return {"ok": True, "clip": clip}


@app.post("/api/clips/{uid}/download")
async def download_clip(uid: str, request: Request):
    """Streams the video file of a ready clip (the Save button)."""
    await require_admin(request)
    try:
        url, name = await clip_manager.download_url(uid)
    except ClipError as exc:
        raise ApiError(exc.status, "clip_download", str(exc))

    async def stream():
        async with clip_manager.http.stream("GET", url) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------ Twitch sign-in

@app.post("/api/auth/twitch/start")
async def twitch_start(request: Request):
    await require_admin(request)
    uri = redirect_uri(request)
    try:
        url = auth.begin_authorization(uri)
    except AuthError as exc:
        raise ApiError(409, exc.code, str(exc))
    return {"ok": True, "url": url, "redirect_uri": uri}


@app.get(CALLBACK_PATH)
async def twitch_callback(request: Request):
    """Twitch sends the person back here after they approve (or refuse) the sign-in."""
    params = request.query_params

    def done(result: str, reason: str | None = None):
        target = f"/?clip_auth={result}" + (f"&reason={reason}" if reason else "")
        return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})

    if params.get("error"):
        return done("error", "denied")
    if not auth.consume_state(params.get("state", "")):
        return done("error", "state")
    code = params.get("code", "")
    if not code:
        return done("error", "exchange")

    try:
        await auth.exchange_code(clip_manager.http, code, redirect_uri(request))
    except AuthError as exc:
        log.warning("Twitch sign-in failed: %s", exc)
        return done("error", exc.code if exc.code in ("scope", "config", "exchange") else "exchange")
    log.info("Twitch account connected for clipping: %s", auth.login)
    return done("ok")


@app.post("/api/auth/twitch/disconnect")
async def twitch_disconnect(request: Request):
    await require_admin(request)
    await auth.disconnect(clip_manager.http)
    clip_manager.auto_enabled = False
    return {"ok": True}


@app.post("/api/auth/twitch/refresh-token")
async def twitch_refresh_token(request: Request):
    """Lets the owner copy the refresh token into the host's settings so the
    connection survives restarts on hosts that wipe the disk."""
    await require_admin(request)
    if not auth.refresh_token:
        raise ApiError(409, "no_refresh_token", "Connect your Twitch account first.")
    return JSONResponse(
        {"ok": True, "refresh_token": auth.refresh_token},
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
