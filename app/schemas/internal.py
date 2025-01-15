from pydantic import BaseModel


class TeamCache(BaseModel):
    id: str
    name: str
