from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.constants import NewsVideoProvider


# Response for `GET /api/v1/news`
class NewsItem(BaseModel):
    url: str
    title: str
    description: str
    date: datetime
    author: str


class NewsTextRun(BaseModel):
    text: str
    url: str | None = None
    bold: bool = False
    italic: bool = False


NewsBlockType = Literal["paragraph", "heading", "blockquote", "list", "list_item", "image", "video", "caption"]


class NewsVideoPlayer(BaseModel):
    provider: NewsVideoProvider
    media_id: str
    player_url: str
    external_url: str


class NewsBlock(BaseModel):
    """A supported article block, optionally containing nested or media content.

    For media, ``url`` is the source while ``link_url`` is its wrapping anchor.
    """

    type: NewsBlockType
    runs: list[NewsTextRun] = Field(default_factory=list)
    children: list[NewsBlock] = Field(default_factory=list)
    level: int | None = None
    ordered: bool = False
    start: int = 1
    url: str | None = None
    link_url: str | None = None
    alt: str = ""
    player: NewsVideoPlayer | None = None


# Response for `GET /api/v1/news/{id}`
class NewsArticle(BaseModel):
    id: str
    title: str
    content: str
    blocks: list[NewsBlock] = Field(default_factory=list)
    links: list[dict[str, str]]
    images: list[str]
    videos: list[str]
    date: datetime | None
    author: str
