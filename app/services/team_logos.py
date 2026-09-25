"""Team logos mirrored to our CDN (see scripts/mirror_team_logos.py), sent alongside VLR's in live push."""

import logging

import httpx2

from app import constants

logger = logging.getLogger(__name__)

_logos: dict[str, str] = {}


async def load() -> None:
    """Load the CDN logo manifest; on failure live push sends VLR's logos only.

    :return: None.
    """
    try:
        async with httpx2.AsyncClient(timeout=10) as client:
            manifest = (await client.get(constants.TEAM_LOGOS_URL)).raise_for_status().json()
        logos = {team_id: entry["logo"]["url"] for team_id, entry in manifest.items()}
    except httpx2.HTTPError, ValueError, KeyError, TypeError:
        logger.warning("could not load team logos from %s", constants.TEAM_LOGOS_URL, exc_info=True)
        return
    _logos.clear()
    _logos.update(logos)
    logger.info("loaded %d team logos", len(_logos))


def logo_url(team_id: str | None) -> str | None:
    """Return the CDN logo for a VLR team.

    :param team_id: VLR team ID.
    :return: Logo URL, or None when the team has no mirrored logo.
    """
    return _logos.get(team_id) if team_id else None
