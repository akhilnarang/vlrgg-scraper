"""Recognize supported provider URLs without fetching arbitrary media."""

import re
from urllib.parse import parse_qs, urlsplit

from app.schemas.news import NewsVideoPlayer

_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_TWITCH_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")


def valid_media_id(provider: str, media_id: str) -> bool:
    pattern = {"youtube": _YOUTUBE_ID, "twitch": _TWITCH_ID}.get(provider)
    return pattern is not None and pattern.fullmatch(media_id) is not None


def news_video_player(url: str) -> NewsVideoPlayer | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port:
            return None
        host = parsed.hostname
        path = parsed.path.strip("/").split("/")
        query = parse_qs(parsed.query)
    except ValueError:
        return None
    provider = None
    media_id = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        provider = "youtube"
        if len(path) == 2 and path[0] == "embed":
            media_id = path[1]
        elif path == ["watch"] and host not in {"youtube-nocookie.com", "www.youtube-nocookie.com"}:
            media_id = query.get("v", [""])[0]
    elif host == "youtu.be" and len(path) == 1:
        provider, media_id = "youtube", path[0]
    elif host == "clips.twitch.tv" and len(path) == 1:
        provider = "twitch"
        media_id = query.get("clip", [""])[0] if path == ["embed"] else path[0]
    elif host in {"twitch.tv", "www.twitch.tv"} and len(path) == 3 and path[1] == "clip":
        provider, media_id = "twitch", path[2]
    if provider is None or not valid_media_id(provider, media_id):
        return None
    external = (
        f"https://www.youtube.com/watch?v={media_id}"
        if provider == "youtube"
        else f"https://clips.twitch.tv/{media_id}"
    )
    return NewsVideoPlayer(
        provider=provider,
        media_id=media_id,
        player_url=f"/media/{provider}/{media_id}",
        external_url=external,
    )
