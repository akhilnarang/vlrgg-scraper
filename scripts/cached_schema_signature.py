"""Print a digest of the JSON schemas of the models whose serialized form Redis caches.

Cached payloads are validated strictly on read, so when this digest changes a deploy
must purge the application cache: an old-schema entry raises and 5xxs until it expires
(see AGENTS.md, "Caching"). CI compares the digest with the target branch and fails so
the purge is remembered instead of silently forgotten.

Usage (from the repo root): ``uv run python -m scripts.cached_schema_signature``.
"""

import argparse
import hashlib
import json

from app.schemas import Event, Match, MatchWithDetails, NewsItem, Player, Ranking, Standings, Team
from app.schemas.matches import VideoScore

CACHED_MODELS = {
    "Match": Match,
    "MatchWithDetails": MatchWithDetails,
    "Event": Event,
    "NewsItem": NewsItem,
    "Ranking": Ranking,
    "Standings": Standings,
    "Team": Team,
    "Player": Player,
    "VideoScore": VideoScore,
}


def signatures() -> dict[str, str]:
    """Digest each cached model's canonical JSON schema.

    :return: Model name to sha256 hex digest.
    """
    return {
        name: hashlib.sha256(
            json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for name, model in CACHED_MODELS.items()
    }


def main() -> None:
    """Print the combined digest, or one line per model with ``--per-model``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-model", action="store_true", help="print one digest per cached model")
    arguments = parser.parse_args()
    digests = signatures()
    if arguments.per_model:
        for name, digest in digests.items():
            print(f"{name} {digest}")
        return
    print(hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest())


if __name__ == "__main__":
    main()
