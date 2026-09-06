"""Recognize supported provider URLs without fetching arbitrary media."""

import re
from urllib.parse import parse_qs, urlsplit

from app.constants import NewsVideoProvider
from app.schemas.news import NewsVideoPlayer

_MEDIA_ID_PATTERNS = {
    NewsVideoProvider.YOUTUBE: re.compile(r"[A-Za-z0-9_-]{11}"),
    NewsVideoProvider.TWITCH: re.compile(r"[A-Za-z0-9_-]{1,200}"),
}


def valid_media_id(provider: NewsVideoProvider, media_id: str) -> bool:
    """Validate a provider's ID syntax without checking whether the media exists.

    :param provider: The hosted video provider.
    :param media_id: The provider-specific identifier to validate.
    :return: Whether the ID matches the provider's supported syntax.
    """
    pattern = _MEDIA_ID_PATTERNS.get(provider)
    return pattern is not None and pattern.fullmatch(media_id) is not None


def news_video_player(url: str) -> NewsVideoPlayer | None:
    """Recognize a video URL and build metadata for its hosted player.

    :param url: Candidate YouTube video or Twitch clip URL.
    :return: Provider metadata, or ``None`` for unsupported or malformed URLs.
    """
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
        provider = NewsVideoProvider.YOUTUBE
        if len(path) == 2 and path[0] == "embed":
            media_id = path[1]
        elif path == ["watch"] and host not in {"youtube-nocookie.com", "www.youtube-nocookie.com"}:
            media_id = query.get("v", [""])[0]
    elif host == "youtu.be" and len(path) == 1:
        provider, media_id = NewsVideoProvider.YOUTUBE, path[0]
    elif host == "clips.twitch.tv" and len(path) == 1:
        provider = NewsVideoProvider.TWITCH
        media_id = query.get("clip", [""])[0] if path == ["embed"] else path[0]
    elif host in {"twitch.tv", "www.twitch.tv"} and len(path) == 3 and path[1] == "clip":
        provider, media_id = NewsVideoProvider.TWITCH, path[2]
    if provider is None or not valid_media_id(provider, media_id):
        return None
    external = (
        f"https://www.youtube.com/watch?v={media_id}"
        if provider is NewsVideoProvider.YOUTUBE
        else f"https://clips.twitch.tv/{media_id}"
    )
    return NewsVideoPlayer(
        provider=provider,
        media_id=media_id,
        player_url=f"/media/{provider.value}/{media_id}",
        external_url=external,
    )
