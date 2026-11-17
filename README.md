# AutoClip — first web prototype

A private FastAPI dashboard that reads a Twitch channel's chat, calibrates its normal
message rate for the first 120 seconds, maintains a rolling baseline, and detects
unusual chat spikes. Automatic Twitch clip requests are optional and disabled by
default until you configure the Twitch API credentials.

## Important Twitch requirements

- Chat reading uses Twitch IRC over a secure WebSocket.
- The Twitch user token used for IRC needs the `chat:read` scope.
- Clip creation uses the Twitch Create Clip API and needs a user token with
  `clips:edit`, plus the matching Client ID.
- The broadcaster must have clips enabled, and their clip restrictions must allow
  your account. AutoClip cannot bypass channel restrictions.
- Twitch recommends EventSub for new chat integrations. IRC is used in this first
  prototype because it is straightforward to test with a viewer account. We can
  migrate the chat connector to EventSub after the basic detection pipeline is proven.

Official docs:
- https://dev.twitch.tv/docs/chat/irc/
- https://dev.twitch.tv/docs/api/reference/#create-clip

## 1. Run locally first

Python 3.12 is recommended.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and fill in:
- `TWITCH_USERNAME`: your Twitch username
- `TWITCH_OAUTH_TOKEN`: a user access token with `chat:read` (token only, no `oauth:` prefix)
- `TWITCH_CHANNEL=jynxzi`

Then start it:

```powershell
python main.py
```

Open http://127.0.0.1:8000

If PowerShell blocks activation, you can run `.venv\Scripts\python.exe main.py` directly.

## 2. Twitch token safety

Never paste your OAuth token into chat, screenshots, GitHub, or the dashboard.
Keep `.env` private. Do not commit it to Git. If a token is exposed, revoke it in
your Twitch account's connections/developer settings and generate a new one.

## 3. First test

- Start the app while Jynxzi is live.
- The first 120 seconds are calibration; no clips are requested.
- After calibration, the dashboard shows the current messages/sec, baseline and
  hype score.
- The baseline uses a five-minute rolling window and updates every ten seconds.
- Detected spikes are excluded from baseline updates to reduce baseline inflation.
- A 90-second cooldown prevents repeated detections for one burst.

## 4. Clipping

Clipping is set up from the dashboard, not by pasting tokens into `.env`.

1. In the [Twitch developer console](https://dev.twitch.tv/console/apps) register an
   application with client type **Confidential**.
2. Add this OAuth redirect URL (use your own address if it is not the hosted one):
   `https://autoclip-oz6y.onrender.com/auth/twitch/callback`
   (locally: `http://127.0.0.1:8000/auth/twitch/callback`).
3. Set `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` and `ADMIN_KEY` (a long random
   text) on the server, then restart or redeploy. On a local machine the admin key
   is optional.
4. Open the dashboard, press **Set up clipping** and choose **Connect Twitch**. Approve
   the `clips:edit` permission with the account that should make the clips.
5. Press **Clip now** to test it, or turn on **Auto-clip** to save a clip on every spike.

Tokens refresh themselves. Hosts that wipe their disk on restart (Render's free
plan does) lose the saved login, so open the setup screen after connecting, press
**Copy refresh token** and save it as `TWITCH_CLIP_REFRESH_TOKEN` in the host's
settings.

Each clip goes through Starting, Saving, then Ready (or Failed with the reason).
Twitch only makes clips while the channel is live, and the streamer's clip
restrictions apply. A clip is only listed as Ready once Twitch has really published it.

## 5. VPS deployment

Do not expose this prototype directly to the public internet yet. Before VPS deployment,
add HTTPS, a login system, secure secret storage, reverse-proxy configuration, and
persistent database storage. The current prototype binds to localhost by default.
