"""Mirror official map images from playvalorant.com for upload to our CDN.

For each map on https://playvalorant.com/en-us/maps/ this saves, at original resolution:

- `{map}-minimap.jpg`: the square callout map (sites, spawns, and callouts).
- `{map}-banner.jpg`: the widest in-game screenshot below 4K.
- `{map}-splash.jpg`: the loading-screen art, which identifies a map at a glance, no wider than
  `SPLASH_WIDTH` px. playvalorant.com doesn't carry it, so it comes from valorant-api.com's `splash`
  (itself sourced from Riot game data), matched by map name.

A `manifest.json` records each file's source URL and dimensions. Rerun when Riot adds a map.

Usage: uv run --with pillow scripts/mirror_map_images.py [output-dir]
"""

import io
import json
import re
import sys
from pathlib import Path

import httpx2
from PIL import Image

MAPS_PAGE = "https://playvalorant.com/en-us/maps/"
VALORANT_API_MAPS = "https://valorant-api.com/v1/maps"
# The splash fills part of a phone-width Live Activity; 1280 px stays sharp at 3x without 2-7 MB originals.
SPLASH_WIDTH = 1280
DEFAULT_CDN_BASE_URL = "https://files.akhilnarang.dev/cdn/valorant/"
DIMENSIONS = re.compile(r"-(\d+)x(\d+)\.\w+$")


def dimensions(url: str) -> tuple[int, int]:
    """Read an image's original size from its file name.

    :param url: Riot CMS image URL ending in `-{width}x{height}.{ext}`.
    :return: Width and height.
    :raises ValueError: If the URL has no dimensions.
    """
    if not (match := DIMENSIONS.search(url)):
        raise ValueError(f"no dimensions in {url}")
    return int(match[1]), int(match[2])


def map_blocks(data: object) -> list[dict]:
    """Find the per-map sections in the page data.

    :param data: Parsed `__NEXT_DATA__` JSON.
    :return: Sections with a `fragmentId` (the map slug) and an image gallery.
    """
    found: list[dict] = []
    if isinstance(data, dict):
        if data.get("fragmentId") and isinstance(data.get("header"), dict) and data.get("groups"):
            found.append(data)
        for value in data.values():
            found.extend(map_blocks(value))
    elif isinstance(data, list):
        for value in data:
            found.extend(map_blocks(value))
    return found


def image_urls(data: object) -> list[str]:
    """List unique image URLs in page order.

    :param data: A map section.
    :return: Image URLs without query strings.
    """
    urls: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            clean = url.split("?")[0] if isinstance(url, str) else ""
            if DIMENSIONS.search(clean) and clean not in urls:
                urls.append(clean)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return urls


def pick(urls: list[str]) -> tuple[str, str]:
    """Choose the minimap and banner images for a map.

    :param urls: A map's image URLs in page order.
    :return: The largest square image and the widest screenshot.
    :raises ValueError: If either image is missing.
    """
    sizes = {url: dimensions(url) for url in urls}
    squares = [url for url, (width, height) in sizes.items() if abs(width - height) <= 4]
    # The first wide image is the aerial key art, which has cut-out edges; screenshots follow it.
    wide = [url for url, (width, height) in sizes.items() if 1.7 <= width / height <= 1.85][1:]
    if not squares or not wide:
        raise ValueError("missing a square minimap or a wide screenshot")
    # On a tie, take the last square: the en-us Abyss gallery lists a Vietnamese callout map first.
    minimap = max(reversed(squares), key=lambda url: sizes[url][0])
    return minimap, max(wide, key=lambda url: sizes[url][0])


def splash_urls(client: httpx2.Client) -> dict[str, str]:
    """Find each map's loading-screen art on valorant-api.com.

    :param client: HTTP client.
    :return: Splash URLs keyed by lowercase map name letters, the form of the maps page slugs.
    """
    maps = client.get(VALORANT_API_MAPS).raise_for_status().json()["data"]
    return {
        re.sub(r"[^a-z]+", "", entry["displayName"].lower()): entry["splash"]
        for entry in maps
        if entry.get("splash") and entry.get("tacticalDescription")
    }


def save_splash(client: httpx2.Client, url: str, path: Path) -> tuple[int, int]:
    """Download a splash and save it as a JPEG no wider than `SPLASH_WIDTH`.

    :param client: HTTP client.
    :param url: Source PNG URL.
    :param path: Output file.
    :return: Saved width and height.
    """
    image = Image.open(io.BytesIO(client.get(url).raise_for_status().content)).convert("RGB")
    image.thumbnail((SPLASH_WIDTH, SPLASH_WIDTH))
    image.save(path, "JPEG", quality=85, optimize=True, progressive=True)
    return image.size


def main() -> None:
    """Download each map's minimap, banner, and splash into the output directory.

    :return: None.
    """
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "map-assets")
    cdn_base_url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CDN_BASE_URL
    output.mkdir(parents=True, exist_ok=True)
    with httpx2.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        page = client.get(MAPS_PAGE).raise_for_status().text
        if not (next_data := re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.DOTALL)):
            raise SystemExit(f"no page data found on {MAPS_PAGE}")
        data = json.loads(next_data[1])
        splashes = splash_urls(client)
        manifest = {}
        for block in map_blocks(data):
            slug = block["fragmentId"]
            minimap, banner = pick(image_urls(block))
            manifest[slug] = {"name": block["header"]["title"].title()}
            for kind, url in (("minimap", minimap), ("banner", banner)):
                path = output / f"{slug}-{kind}.jpg"
                path.write_bytes(client.get(url, params={"fm": "jpg", "q": 90}).raise_for_status().content)
                width, height = dimensions(url)
                manifest[slug][kind] = {
                    "url": f"{cdn_base_url.rstrip('/')}/{path.name}",
                    "file": path.name,
                    "width": width,
                    "height": height,
                    "upstream_source": url,
                }
            if splash := splashes.get(slug):
                path = output / f"{slug}-splash.jpg"
                width, height = save_splash(client, splash, path)
                manifest[slug]["splash"] = {
                    "url": f"{cdn_base_url.rstrip('/')}/{path.name}",
                    "file": path.name,
                    "width": width,
                    "height": height,
                    "upstream_source": splash,
                }
            else:
                print(f"{slug}: no splash on valorant-api.com")
            print(f"{slug}: minimap {manifest[slug]['minimap']['width']}px, banner {banner.rsplit('-', 1)[-1]}")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(manifest)} maps -> {output}")


if __name__ == "__main__":
    main()
