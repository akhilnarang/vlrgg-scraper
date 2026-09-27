from http import HTTPStatus

from fastapi import APIRouter, BackgroundTasks

from app import constants
from app.api.deps import RedisDep
from app.cron import live_push
from app.schemas.matches import VideoScore

router = APIRouter()


@router.put("/score", status_code=HTTPStatus.NO_CONTENT)
async def store_video_score(video: VideoScore, background: BackgroundTasks, client: RedisDep) -> None:
    """Store the broadcast tracker's score and push its match when the score changed."""
    previous = await client.set(
        constants.VIDEO_SCORE_KEY, video.model_dump_json(), ex=constants.VIDEO_SCORE_TTL, get=True
    )
    if (stored := VideoScore.from_cache(previous)) is None or not stored.same_score(video):
        background.add_task(live_push.push_video_match)
