from pydantic import BaseModel

# Response for `GET /api/v1/version/


class VersionResponse(BaseModel):
    event_list: int
    event_details: int
    match_list: int
    match_details: int
    news_list: int
    player_details: int
    rankings_list: int
    team_details: int
