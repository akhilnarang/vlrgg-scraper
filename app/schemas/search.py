from pydantic import BaseModel, HttpUrl

from app import constants


class SearchResult(BaseModel):
    id: str
    name: str
    img: HttpUrl
    category: constants.SearchCategory
    description: str | None = None
