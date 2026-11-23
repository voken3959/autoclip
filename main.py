import asyncio
import hmac
import logging
import json
import os
import re
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
from core.badges import BadgeStore
from core.viewers import ViewerTracker
from dashboard.page import PAGE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("autoclip")

CALLBACK_PATH = "/auth/twitch/callback"

# The channel can be changed from the dashboard. The choice is saved to a small
# file so it survives restarts; TWITCH_CHANNEL is the starting/default channel.
DEFAULT_CHANNEL = os.getenv("TWITCH_CHANNEL", "jynxzi").strip().lstrip("#").lower()
CHANNEL_STORE = Path(os.getenv("CHANNEL_STORE_PATH", ".autoclip_channel.json"))
CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,25}$")


def normalize_channel(value) -> str:
    """'#Name', '@name' or a twitch.tv link -> 'name' (empty if it isn't valid)."""
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^(www\.|m\.)?twitch\.tv/", "", text)
    text = text.split("/")[0].split("?")[0].lstrip("#@")
    return text if CHANNEL_RE.match(text) else ""


def load_saved_channel() -> str:
    try:
        saved = json.loads(CHANNEL_STORE.read_text(encoding="utf-8")).get("channel")
    except (OSError, ValueError, AttributeError):
        return ""
    return normalize_channel(saved)


def save_channel(name: str) -> None:
    try:
        tmp = CHANNEL_STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"channel": name}), encoding="utf-8")
        os.replace(tmp, CHANNEL_STORE)
    except OSError as exc:
        log.warning("Couldn't save the channel choice: %s", exc)


CHANNEL = load_saved_channel() or DEFAULT_CHANNEL


def env_broadcaster_id() -> str:
    """TWITCH_BROADCASTER_ID only describes the TWITCH_CHANNEL channel."""
    if CHANNEL != DEFAULT_CHANNEL:
        return ""
    return os.getenv("TWITCH_BROADCASTER_ID", "").strip()


monitor = Monitor(CHANNEL)
auth = TwitchAuth()
# TWITCH_BROADCASTER_ID (if set) is used straight away; otherwise the channel's
# ID is read from Twitch chat once we have joined it (also used for 7TV emotes).
emotes = SevenTVEmotes(channel_id=env_broadcaster_id())
clip_manager = ClipManager(
    monitor,
    auth,
    get_broadcaster_id=lambda: env_broadcaster_id() or emotes.channel_id,
    channel=CHANNEL,
)
# Twitch badges shown next to usernames in the dashboard chat. The badge
# images come from the Helix chat-badges APIs (same client keys as the
# leaderboard); the watched channel's ID feeds the channel-badge lookup.
badges = BadgeStore(
    client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
    client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
    get_channel_id=lambda: emotes.channel_id,
)
badges.set_channel_id(env_broadcaster_id())
monitor.badges_resolver = badges.resolve


def note_room_id(room_id: str) -> None:
    """The channel's ID arrives from Twitch chat: feed it to emotes and badges."""
    emotes.set_channel_id(room_id)
    badges.set_channel_id(room_id)


viewers = ViewerTracker(CHANNEL)
chat_client = TwitchChatClient(
    username=os.getenv("TWITCH_USERNAME", "").strip(),
    token=os.getenv("TWITCH_OAUTH_TOKEN", "").strip(),
    channel=CHANNEL,
    monitor=monitor,
    on_room_id=note_room_id,
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
    data["viewers"] = viewers.summary()
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


async def switch_channel(name: str) -> bool:
    """Point everything at another Twitch channel. Returns False if unchanged."""
    global CHANNEL
    if name == CHANNEL:
        return False
    CHANNEL = name
    save_channel(name)
    log.info("Switching to #%s", name)

    monitor.switch_channel(name)
    viewers.set_channel(name)
    emotes.reset_channel()
    badges.reset_channel()
    clip_manager.set_channel(name)
    # Clipping needs permission on the channel being watched, so don't keep
    # auto-clipping a channel nobody has checked it for.
    clip_manager.auto_enabled = False

    known_id = env_broadcaster_id()
    if known_id:
        note_room_id(known_id)
    await chat_client.set_channel(name)

    await broadcast({"type": "channel_changed", "channel": name})
    await broadcast(snapshot())
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks.append(asyncio.create_task(broadcast_loop()))
    tasks.append(asyncio.create_task(emotes.run()))
    tasks.append(asyncio.create_task(viewers.run()))
    tasks.append(asyncio.create_task(auth.run(clip_manager.http)))
    tasks.append(asyncio.create_task(leaderboard.run()))
    tasks.append(asyncio.create_task(badges.run()))
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
    await badges.aclose()
    await clip_manager.aclose()
    await viewers.aclose()


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


@app.get("/api/viewers")
async def viewers_endpoint():
    """The channel's viewer count and its last 24 hours, one sample a minute."""
    return JSONResponse(viewers.payload(), headers={"Cache-Control": "no-store"})


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


@app.post("/api/channel")
async def set_channel(request: Request):
    """Change the watched Twitch channel (needs the admin key, like clipping)."""
    await require_admin(request)
    body = await read_json(request)
    name = normalize_channel(body.get("channel"))
    if not name:
        raise ApiError(400, "bad_channel", "Enter a Twitch channel name, like jynxzi (letters, numbers and underscores).")
    changed = await switch_channel(name)
    return {"ok": True, "channel": CHANNEL, "changed": changed}


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
        url, name, extra_headers = await clip_manager.download_url(uid)
    except ClipError as exc:
        raise ApiError(exc.status, "clip_download", str(exc))

    # Open the video first, so a refusal is reported as a message instead of
    # being saved as a broken .mp4 file.
    try:
        upstream = await clip_manager.http.send(
            clip_manager.http.build_request("GET", url, headers=extra_headers or None),
            stream=True,
        )
    except httpx.HTTPError:
        raise ApiError(502, "clip_download", "Couldn't reach Twitch's video server. Try again in a moment.")
    if upstream.status_code != 200:
        status = upstream.status_code
        await upstream.aclose()
        raise ApiError(502, "clip_download", f"Twitch's video server refused the download (error {status}). Try again in a minute.")

    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()  # also runs if the browser stops the download

    return StreamingResponse(
        stream(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            # A video is already compressed; stop the gzip layer wasting time on it.
            "Content-Encoding": "identity",
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
