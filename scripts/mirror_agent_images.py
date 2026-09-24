"""Mirror official agent portraits and icons for upload to our CDN.

Portraits come from Riot's own CMS via https://playvalorant.com/en-us/agents/
(the same source as the map images). The official Riot API (val-content-v1)
carries character names and ids but zero image URLs, so small square icons are
mirrored from valorant-api.com displayIcon assets (themselves sourced from Riot
game data) and served from our CDN from then on.

For each agent this saves, at original resolution:

- `{key}-portrait.png`: the official 616x822 full portrait.
- `{key}-icon.png`: the square head icon.

`key` is the lowercased alphanumeric agent name (`kayo` for KAY/O), matching
how VLR labels agent images. An `agents.json` manifest records each file's
source URL and dimensions. Rerun when Riot adds an agent.

Usage: uv run scripts/mirror_agent_images.py [output-dir]
"""

import json
import re
import sys
from pathlib import Path

import httpx2

AGENTS_PAGE = "https://playvalorant.com/en-us/agents/"
VALORANT_API_AGENTS = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"
DEFAULT_CDN_BASE_URL = "https://files.akhilnarang.dev/cdn/valorant/"
DIMENSIONS = re.compile(r"-(\d+)x(\d+)\.\w+$")


def png_dimensions(content: bytes) -> tuple[int, int]:
    """Read width and height from a PNG's IHDR chunk.

    :param content: Raw PNG bytes.
    :return: Width and height.
    :raises ValueError: If the bytes are not a PNG.
    """
    import struct

    if content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", content[16:24])


def key(name: str) -> str:
    """Normalize an agent name to its CDN key.

    :param name: Display name (e.g. "KAY/O").
    :return: Lowercase alphanumeric key (e.g. "kayo").
    """
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def main() -> None:
    """Download each agent's portrait and icon into the output directory.

    :return: None.
    """
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "agent-assets")
    cdn_base_url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CDN_BASE_URL
    output.mkdir(parents=True, exist_ok=True)
    with httpx2.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        page = client.get(AGENTS_PAGE).raise_for_status().text
        if not (next_data := re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.DOTALL)):
            raise SystemExit(f"no page data found on {AGENTS_PAGE}")
        data = json.loads(next_data[1])
        grid = next(b for b in data["props"]["pageProps"]["page"]["blades"] if b.get("type") == "characterCardGrid")
        portraits = {}
        for item in grid["items"]:
            slug = item["action"]["payload"]["url"].rstrip("/").rsplit("/", 1)[-1]
            portraits[key(item["title"])] = {
                "name": item["title"],
                "slug": slug,
                "url": item["media"]["url"].split("?")[0],
                "width": item["media"]["dimensions"]["width"],
                "height": item["media"]["dimensions"]["height"],
            }
        icons = {
            key(a["displayName"]): {"name": a["displayName"], "url": a["displayIcon"], "uuid": a["uuid"]}
            for a in client.get(VALORANT_API_AGENTS).raise_for_status().json()["data"]
            if a.get("displayIcon")
        }
        manifest = {}
        for agent_key, portrait in sorted(portraits.items()):
            icon = icons.get(agent_key)
            if icon is None:
                print(f"{agent_key}: no icon found, skipping")
                continue
            entry = {"name": portrait["name"], "slug": portrait["slug"], "uuid": icon["uuid"]}
            for kind, url in (("portrait", portrait["url"]), ("icon", icon["url"])):
                path = output / f"{agent_key}-{kind}.png"
                content = client.get(url).raise_for_status().content
                path.write_bytes(content)
                if match := DIMENSIONS.search(url):
                    width, height = int(match[1]), int(match[2])
                else:
                    width, height = png_dimensions(content)
                entry[kind] = {
                    "url": f"{cdn_base_url.rstrip('/')}/{path.name}",
                    "file": path.name,
                    "width": width,
                    "height": height,
                    "upstream_source": url,
                }
            manifest[agent_key] = entry
            print(f"{agent_key}: portrait {entry['portrait']['width']}px, icon saved")
    (output / "agents.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(manifest)} agents -> {output}")


if __name__ == "__main__":
    main()
