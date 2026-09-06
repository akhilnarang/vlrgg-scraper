"""Public, fixed-provider embed pages for native mobile WebViews."""

from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.constants import NewsVideoProvider
from app.exceptions import NotFoundError
from app.services.news_video import valid_media_id

router = APIRouter()


@router.get("/media/{provider}/{media_id}", response_class=HTMLResponse)
async def media_player(request: Request, provider: NewsVideoProvider, media_id: str) -> HTMLResponse:
    """Serve a provider iframe page for a syntactically valid media ID.

    :param request: The request used to derive Twitch's parent hostname.
    :param provider: Supported provider name (``youtube`` or ``twitch``).
    :param media_id: Provider-specific media identifier.
    :return: A secured HTML response containing the official provider iframe.
    :raises NotFoundError: For an invalid media ID.
    """
    if not valid_media_id(provider, media_id):
        raise NotFoundError("Unsupported video")
    if provider is NewsVideoProvider.YOUTUBE:
        query = urlencode({"playsinline": "1", "autoplay": "0"})
        source = f"https://www.youtube.com/embed/{media_id}?{query}"
        title = "YouTube video player"
        frame_origin = "https://www.youtube.com"
        min_width, min_height = 200, 200
    else:
        query = urlencode({"clip": media_id, "parent": request.url.hostname, "autoplay": "false"})
        source = f"https://clips.twitch.tv/embed?{query}"
        title = "Twitch clip player"
        frame_origin = "https://clips.twitch.tv"
        min_width, min_height = 400, 300
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>html,body{{margin:0;width:100%;height:100%;background:#000}}
iframe{{display:block;border:0;width:100%;height:100%;min-width:{min_width}px;min-height:{min_height}px}}</style>
</head><body><iframe src="{escape(source, quote=True)}" title="{title}"
allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></body></html>'''
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; frame-src {frame_origin}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "X-Content-Type-Options": "nosniff",
        },
    )
