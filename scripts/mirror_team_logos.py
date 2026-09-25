"""Mirror official team logos for upload to our CDN, and record each partner team's Riot fields.

VLR serves 200 px logos. Riot's Valorant esports web API lists each league's
teams with the logo Riot uses on valorantesports.com, often 1000 px or more.
This collects the partner teams of the VCT leagues, the teams in the current
season's regular-season weeks (Play-Ins and older seasons also list
Ascension and relegated teams), and matches each to its VLR team: by
`OVERRIDES`, then by tag, then by name, all folded to lowercase ASCII letters
and digits. Tags and names come from the `teams` and `id_map` tables; a tag or
name that folds to more than one VLR team is never used.

For each match it saves the logo as `teams/{vlr_id}.png`, no larger than
`MAX_SIZE` px, writes the team's `riot_code`, `riot_name` and `riot_logo`, and
records it in a `teams.json` manifest with the logo's CDN URL and dimensions.
Teams it cannot match are listed so they can be added to `OVERRIDES`. Rerun
when the leagues change, then upload the output directory to the CDN.

Usage (where the app runs, from the repo root):
    uv run --with pillow python -m scripts.mirror_team_logos [output-dir] [cdn-base-url]
"""

import asyncio
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

import httpx2
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.constants import IdMapKind
from app.core.config import settings
from app.db.engine import create_engine
from app.db.models import IdMapping, Team
from app.services import scrape_store

API = "https://esports-api.service.valorantesports.com/persisted/val/getSchedule"
# Public key the valorantesports.com site sends with every request; the site injects it from its build environment,
# so it is not in the page or its scripts to read at run time.
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LEAGUES = {
    "109974795266458277": "vct_americas",
    "106109559530232966": "vct_emea",
    "109974804058058602": "vct_pacific",
    "111691194187846945": "vct_china",
}
MAX_PAGES_PER_LEAGUE = 6  # schedule pages hold 80 events; a season is two to four
REGULAR_SEASON_BLOCK = "Week"
DEFAULT_CDN_BASE_URL = "https://files.akhilnarang.dev/cdn/valorant/"
MAX_SIZE = 512
# Riot code -> VLR team ID, for teams whose Riot name differs from VLR's and whose VLR tag isn't stored yet.
OVERRIDES = {
    "KRU": "2355",  # KRÜ Esports
    "LEV": "2359",  # Leviatán
    "NAVI": "4915",  # Natus Vincere
    "TYL": "731",  # TYLOO
}


def normalize(name: str) -> str:
    """Fold a team name to lowercase ASCII letters and digits, so accents, spaces and punctuation don't matter.

    :param name: Team name, tag, or id_map key.
    :return: The folded name (e.g. "KRÜ Esports" -> "kruesports").
    """
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name).lower())


def unique_index(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Index VLR team IDs by folded name or tag, leaving out any that fold to more than one team.

    :param pairs: (name or tag, VLR team ID) pairs.
    :return: VLR team ID keyed by folded name or tag.
    """
    teams: dict[str, set[str]] = {}
    for name, team_id in pairs:
        teams.setdefault(normalize(name), set()).add(team_id)
    return {name: next(iter(ids)) for name, ids in teams.items() if name and len(ids) == 1}


async def vlr_indexes(session: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    """Read VLR team tags and names from the database.

    :param session: Database session.
    :return: VLR team IDs by folded tag, and by folded name.
    """
    teams = (await session.execute(select(Team.id, Team.tag, Team.name))).all()
    id_map = (await session.execute(select(IdMapping.key, IdMapping.id).where(IdMapping.kind == IdMapKind.TEAM))).all()
    tags = unique_index([(tag, team_id) for team_id, tag, _ in teams if tag])
    names = unique_index([(name, team_id) for team_id, _, name in teams if name] + [tuple(row) for row in id_map])
    return tags, names


def league_teams(client: httpx2.Client, league_id: str) -> dict[str, dict]:
    """Collect the teams in a league's latest regular season.

    :param client: HTTP client.
    :param league_id: Riot league ID.
    :return: Team entries (`code`, `name`, `image`) keyed by Riot code.
    """
    events = []
    page_token = None
    for _ in range(MAX_PAGES_PER_LEAGUE):
        params = {"hl": "en-US", "sport": "val", "leagueId": league_id}
        if page_token:
            params["pageToken"] = page_token
        schedule = client.get(API, params=params).raise_for_status().json()["data"]["schedule"]
        events = schedule["events"] + events
        season = max(event["startTime"][:4] for event in events)
        if events[0]["startTime"][:4] < season or not (page_token := (schedule.get("pages") or {}).get("older")):
            break
    teams: dict[str, dict] = {}
    for event in events:
        if event["startTime"][:4] != season or not event.get("blockName", "").startswith(REGULAR_SEASON_BLOCK):
            continue
        for team in (event.get("match") or {}).get("teams", []):
            if team["code"] != "TBD" and team.get("image"):
                teams[team["code"]] = {**team, "name": team["name"].strip()}
    return teams


async def main() -> None:
    """Download each partner team's logo into the output directory and store its Riot fields.

    :return: None.
    """
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "team-assets")
    cdn_base_url = (sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CDN_BASE_URL).rstrip("/")
    (output / "teams").mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.DATABASE_URL)
    sessions = async_sessionmaker(engine)
    async with sessions() as session:
        tags, names = await vlr_indexes(session)
    manifest: dict[str, dict] = {}
    unmatched = []
    with httpx2.Client(timeout=30, follow_redirects=True, headers={"x-api-key": API_KEY}) as client:
        for league_id, league in LEAGUES.items():
            for code, team in sorted(league_teams(client, league_id).items()):
                vlr_id = OVERRIDES.get(code) or tags.get(normalize(code)) or names.get(normalize(team["name"]))
                if not vlr_id:
                    unmatched.append(f"{code} ({team['name']}, {league})")
                    continue
                source = team["image"].replace("http://", "https://", 1)
                logo = Image.open(io.BytesIO(client.get(source).raise_for_status().content)).convert("RGBA")
                logo.thumbnail((MAX_SIZE, MAX_SIZE))
                path = output / "teams" / f"{vlr_id}.png"
                logo.save(path, optimize=True)
                manifest[vlr_id] = {
                    "name": team["name"],
                    "riot_code": code,
                    "league": league,
                    "logo": {
                        "url": f"{cdn_base_url}/teams/{path.name}",
                        "file": f"teams/{path.name}",
                        "width": logo.width,
                        "height": logo.height,
                        "upstream_source": source,
                    },
                }
                print(f"{code}: VLR {vlr_id}, {logo.width}x{logo.height}")
    async with sessions.begin() as session:
        for vlr_id, entry in manifest.items():
            await scrape_store.upsert_team(
                session,
                vlr_id,
                riot_code=entry["riot_code"],
                riot_name=entry["name"],
                riot_logo=entry["logo"]["upstream_source"],
            )
    await engine.dispose()
    (output / "teams.json").write_text(
        json.dumps(dict(sorted(manifest.items(), key=lambda i: int(i[0]))), indent=2) + "\n"
    )
    print(f"{len(manifest)} teams -> {output}")
    if unmatched:
        print("No VLR ID (add to OVERRIDES):", ", ".join(unmatched))


if __name__ == "__main__":
    asyncio.run(main())
