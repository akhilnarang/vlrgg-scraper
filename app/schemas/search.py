from pydantic import BaseModel, HttpUrl, computed_field

from app import i18n
from app.enums import SearchCategory


class SearchResult(BaseModel):
    id: str
    name: str
    img: HttpUrl
    category: SearchCategory
    description: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def category_label(self) -> str:
        return i18n.label("search_category", self.category)
