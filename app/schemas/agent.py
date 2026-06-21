from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Body for POST /ask: a free-text Valorant question."""

    query: str = Field(min_length=1, max_length=2000)


class AskResponse(BaseModel):
    """Answer for POST /ask. `tools_used` and `model` are populated only when LLM_DEBUG is on."""

    answer: str
    tools_used: list[dict] | None = None
    model: str | None = None
