"""Synthetic live match for on-demand end-to-end testing of live push.

`POST /api/v1/live-updates/test-match` sets the tick key; each cron run then
advances one tick until the match goes final and the key is removed.
"""

from redis.asyncio import Redis

from app import constants
from app.schemas.matches import MatchWithDetails


async def next_observation(client: Redis) -> MatchWithDetails:
    """Advance and build one synthetic match observation.

    :param client: Redis client holding the test tick.
    :return: Synthetic match details.
    """
    tick = int(await client.incr(constants.TEST_TICK_KEY))
    if tick >= 6:
        await client.delete(constants.TEST_TICK_KEY)
    alpha, beta = tick, tick - 1
    image = "https://www.vlr.gg/img/vlr/logo_header.png"
    return MatchWithDetails.model_validate(
        {
            "teams": [
                {"id": "test-alpha", "name": "Test Alpha", "tag": "TA", "score": alpha, "img": image},
                {"id": "test-beta", "name": "Test Beta", "tag": "TB", "score": beta, "img": image},
            ],
            "bans": [],
            "event": {
                "id": constants.TEST_MATCH_ID,
                "img": image,
                "series": "Test",
                "stage": "Test",
                "status": "final" if tick >= 6 else "live",
            },
            "videos": {"streams": [], "vods": []},
            "map_count": 1,
            "total_maps": 3,
            "data": [
                {
                    "number": 1,
                    "map": "Test Range",
                    "teams": [{"name": "Test Alpha", "score": alpha}, {"name": "Test Beta", "score": beta}],
                    "members": [],
                    "rounds": [
                        {
                            "round_number": n,
                            "round_score": f"{(n + 1) // 2}-{n // 2}",
                            "winner": constants.RoundWinner.TEAM1 if n % 2 else constants.RoundWinner.TEAM2,
                            "side": "attack" if n % 2 else "defense",
                            "win_type": "Elimination",
                        }
                        for n in range(1, alpha + beta + 1)
                    ],
                }
            ],
            "previous_encounters": [],
        }
    )
