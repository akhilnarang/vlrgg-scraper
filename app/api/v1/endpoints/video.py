from http import HTTPStatus

from fastapi import APIRouter, BackgroundTasks

from app import constants
from app.api.deps import RedisDep
from app.cron import live_push
from app.exceptions import NotFoundError
from app.schemas.matches import VideoContext, VideoContextCodes, VideoScore
from app.services import push

router = APIRouter()


@router.put("/score", status_code=HTTPStatus.NO_CONTENT)
async def store_video_score(video: VideoScore, background: BackgroundTasks, client: RedisDep) -> None:
    """Store broadcast tracker score, pushing updates or re-sending on heartbeat."""
    previous = await client.set(
        constants.VIDEO_SCORE_KEY, video.model_dump_json(), ex=constants.VIDEO_SCORE_TTL, get=True
    )
    if (stored := VideoScore.from_cache(previous)) is None or not stored.same_score(video):
        # A changed score push restarts the match refresh window.
        background.add_task(live_push.push_video_match)
    else:
        # Re-send unchanged score on heartbeats to heal dropped pushes, rate-limited in the task.
        background.add_task(live_push.refresh_video_match)


@router.get("/context")
async def video_context(client: RedisDep, codes: VideoContextCodes = "") -> VideoContext:
    """Resolve the tracker's match and the series' ordered map names."""
    # Resolve by team codes when provided to support context lookups before scores exist.
    if codes:
        resolved = await push.resolve_video_match_by_codes(client, [code.casefold() for code in codes.split(",")])
        if resolved is None:
            raise NotFoundError("No unambiguous match fits the given team codes")
        match_id, detail = resolved
        return VideoContext(
            match_id=match_id,
            map_order=push.ordered_map_names(detail),
            teams=[team.tag or team.name for team in detail.teams],
        )
    video = await push.video_score(client)
    if video is None:
        raise NotFoundError("No video score is stored")
    resolved = await push.resolve_video_match(client, video)
    if resolved is None:
        raise NotFoundError("No match fits the stored video score")
    match_id, detail, _ = resolved
    return VideoContext(
        match_id=match_id,
        map_number=video.map_number,
        map_order=push.ordered_map_names(detail),
        teams=[team.tag or team.name for team in detail.teams],
    )
