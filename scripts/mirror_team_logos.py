"""Mirror official team logos for upload to our CDN, keyed by VLR team ID.

VLR serves 200 px logos. Riot's Valorant esports web API lists each league's
teams with the logo Riot uses on valorantesports.com, often 1000 px or more.
This collects the partner teams of the VCT leagues, the teams in the current
season's regular-season weeks (Play-Ins and older seasons also list
Ascension and relegated teams),
matches each to its VLR team ID by name (then by tag), folded to lowercase
ASCII letters and digits, using the ID map the scraper keeps in Redis, and saves the logo as a PNG no larger than
`MAX_SIZE` px.

Export the ID map first, on the server:

    redis-cli --json hgetall team > team-ids.json

This writes `teams/{vlr_id}.png` and a `teams.json` manifest recording each
team's name, Riot code, league, and the logo's source URL and dimensions.
Teams it cannot match are listed so they can be added to `OVERRIDES`. Rerun
when the leagues change.

Usage: uv run --with pillow scripts/mirror_team_logos.py team-ids.json [output-dir] [cdn-base-url]
"""

import io
import json
import re
import sys
import unicodedata
from pathlib import Path

import httpx2
from PIL import Image

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
# Riot code -> VLR team ID, for teams whose Riot name and code differ from VLR's.
OVERRIDES = {
    "KRU": "2355",  # KRÜ Esports
    "LEV": "2359",  # Leviatán
    "NAVI": "4915",  # Natus Vincere
    "TYL": "731",  # TYLOO
}


def normalize(name: str) -> str:
    """Fold a team name to lowercase ASCII letters and digits, so accents, spaces and punctuation don't matter.

    :param name: Team name, tag, or Redis ID map key.
    :return: The folded name (e.g. "KRÜ Esports" -> "kruesports").
    """
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name).lower())


def name_index(vlr_ids: dict[str, str]) -> dict[str, str]:
    """Index VLR team IDs by folded name, leaving out names that fold to more than one team.

    :param vlr_ids: The scraper's Redis ID map (simplified name -> VLR team ID).
    :return: VLR team ID keyed by folded name.
    """
    teams: dict[str, set[str]] = {}
    for name, team_id in vlr_ids.items():
        teams.setdefault(normalize(name), set()).add(team_id)
    return {name: next(iter(ids)) for name, ids in teams.items() if name and len(ids) == 1}


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


def main() -> None:
    """Download each partner team's logo into the output directory.

    :return: None.
    """
    vlr_ids = name_index(json.loads(Path(sys.argv[1]).read_text()))
    output = Path(sys.argv[2] if len(sys.argv) > 2 else "team-assets")
    cdn_base_url = (sys.argv[3] if len(sys.argv) > 3 else DEFAULT_CDN_BASE_URL).rstrip("/")
    (output / "teams").mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    unmatched = []
    with httpx2.Client(timeout=30, follow_redirects=True, headers={"x-api-key": API_KEY}) as client:
        for league_id, league in LEAGUES.items():
            for code, team in sorted(league_teams(client, league_id).items()):
                vlr_id = OVERRIDES.get(code) or vlr_ids.get(normalize(team["name"])) or vlr_ids.get(normalize(code))
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
    (output / "teams.json").write_text(
        json.dumps(dict(sorted(manifest.items(), key=lambda i: int(i[0]))), indent=2) + "\n"
    )
    print(f"{len(manifest)} teams -> {output}")
    if unmatched:
        print("No VLR ID (add to OVERRIDES):", ", ".join(unmatched))


if __name__ == "__main__":
    main()
