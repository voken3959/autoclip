import os
import logging
import httpx

log = logging.getLogger(__name__)


class ClipManager:
    async def create_clip(self, title: str):
        enabled = os.getenv("AUTO_CLIP_ENABLED", "false").lower() == "true"
        client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
        token = os.getenv("TWITCH_CLIP_TOKEN", "").strip()
        broadcaster_id = os.getenv("TWITCH_BROADCASTER_ID", "").strip()
        if not enabled:
            log.info("Clip request skipped: AUTO_CLIP_ENABLED is false.")
            return None
        if not all((client_id, token, broadcaster_id)):
            log.error("Auto clipping enabled but Twitch clip credentials are incomplete.")
            return None

        headers = {
            "Client-Id": client_id,
            "Authorization": f"Bearer {token.removeprefix('oauth:')}",
        }
        params = {"broadcaster_id": broadcaster_id, "title": title[:140], "duration": 30}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    "https://api.twitch.tv/helix/clips",
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                data = response.json().get("data", [])
                if not data:
                    return None
                clip_id = data[0]["id"]
                return {
                    "id": clip_id,
                    "url": f"https://clips.twitch.tv/{clip_id}",
                    "edit_url": data[0].get("edit_url"),
                    "title": title,
                    "status": "processing",
                }
        except httpx.HTTPStatusError as exc:
            log.error("Twitch clip API error %s: %s", exc.response.status_code, exc.response.text)
        except Exception:
            log.exception("Clip creation failed")
        return None
