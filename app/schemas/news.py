from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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
    provider: Literal["youtube", "twitch"]
    media_id: str
    player_url: str
    external_url: str


class NewsBlock(BaseModel):
    type: NewsBlockType
    runs: list[NewsTextRun] = Field(default_factory=list)
    children: list[NewsBlock] = Field(default_factory=list)
    level: int | None = None
    ordered: bool = False
    start: int = 1
    url: str | None = None
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
