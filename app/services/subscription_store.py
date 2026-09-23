"""Minimal SQLite store for push tokens, favorites, and match channels."""

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import constants
from app.constants import FavoriteType, Platform
from app.db.models import Client, DeviceToken, Favorite, LiveActivityStart, MatchPushState
from app.exceptions import NotFoundError
from app.schemas.matches import Favorites
from app.services.push import Routing


class SubscriptionStore:
    """Concrete store used by the API and live-push cron."""

    def __init__(self, session: AsyncSession):
        """Bind operations to the caller's database session.

        :param session: Request- or match-scoped database session.
        :return: None.
        """
        self._session = session

    async def register_token(self, client_id: str, token: str, platform: Platform = Platform.IOS) -> None:
        """Store a client's validated push token.

        :param client_id: Client UUID.
        :param token: Validated APNs (lowercase hex) or FCM token.
        :param platform: Platform the token belongs to.
        :return: None.
        """
        if await self._session.get(Client, client_id) is None:
            self._session.add(Client(id=client_id))
        row = await self._session.get(DeviceToken, client_id)
        if row is None:
            self._session.add(DeviceToken(client_id=client_id, token=token, platform=platform))
        else:
            row.token = token
            row.platform = platform

    async def delete_token(self, client_id: str) -> None:
        """Delete a client's push token if it exists.

        :param client_id: Client UUID.
        :return: None.
        """
        row = await self._session.get(DeviceToken, client_id)
        if row is not None:
            await self._session.delete(row)

    async def clear_token(self, token: str) -> None:
        """Clear a rejected APNs token across clients.

        :param token: APNs token rejected by the provider.
        :return: None.
        """
        await self._session.execute(delete(DeviceToken).where(DeviceToken.token == token))

    async def replace_favorites(self, client_id: str, favorites: Favorites) -> None:
        """Replace a client's validated favorites with canonical IDs.

        :param client_id: Client UUID.
        :param favorites: Validated favorites.
        :return: None.
        """
        rows: list[tuple[str, str]] = []
        for entity_type, key in constants.FAVORITE_GROUPS.items():
            for value in dict.fromkeys(str(int(value)) for value in getattr(favorites, key)):
                rows.append((entity_type.value, value))
        if await self._session.get(Client, client_id) is None:
            self._session.add(Client(id=client_id))
        await self._session.execute(delete(Favorite).where(Favorite.client_id == client_id))
        self._session.add_all(
            Favorite(client_id=client_id, entity_type=entity_type, entity_id=entity_id)
            for entity_type, entity_id in rows
        )

    async def get_favorites(self, client_id: str) -> Favorites:
        """Read a client's stored favorites by group.

        :param client_id: Client UUID.
        :return: Stored favorites, each group sorted numerically.
        :raises NotFoundError: If the client is not registered.
        """
        if await self._session.get(Client, client_id) is None:
            raise NotFoundError("Client is not registered")
        rows = (
            await self._session.execute(
                select(Favorite.entity_type, Favorite.entity_id).where(Favorite.client_id == client_id)
            )
        ).all()
        groups: dict[str, list[str]] = {key: [] for key in constants.FAVORITE_GROUPS.values()}
        for entity_type, entity_id in rows:
            groups[constants.FAVORITE_GROUPS[FavoriteType(entity_type)]].append(entity_id)
        return Favorites(**{key: sorted(ids, key=int) for key, ids in groups.items()})

    @staticmethod
    def _favorite_filter(routing: Routing):
        """Build the match, event, team, or player favorite predicate.

        :param routing: The match routing IDs.
        :return: SQL predicate matching any favorite for the match.
        """
        clauses = [(Favorite.entity_type == FavoriteType.MATCH.value) & (Favorite.entity_id == routing.match_id)]
        if routing.event_id:
            clauses.append(
                (Favorite.entity_type == FavoriteType.EVENT.value) & (Favorite.entity_id == routing.event_id)
            )
        if routing.team_ids:
            clauses.append((Favorite.entity_type == FavoriteType.TEAM.value) & Favorite.entity_id.in_(routing.team_ids))
        if routing.player_ids:
            clauses.append(
                (Favorite.entity_type == FavoriteType.PLAYER.value) & Favorite.entity_id.in_(routing.player_ids)
            )
        return or_(*clauses)

    async def pending_starts(self, routing: Routing) -> list[tuple[str, str]]:
        """Find matching clients that have not received a start attempt.

        :param routing: The match routing IDs.
        :return: iOS client IDs and APNs tokens awaiting a start.
        """
        started = exists(
            select(LiveActivityStart.id).where(
                LiveActivityStart.client_id == Client.id,
                LiveActivityStart.match_id == routing.match_id,
            )
        )
        rows = (
            await self._session.execute(
                select(Client.id, DeviceToken.token)
                .join(DeviceToken, DeviceToken.client_id == Client.id)
                .join(Favorite, Favorite.client_id == Client.id)
                .where(DeviceToken.platform == Platform.IOS, self._favorite_filter(routing), ~started)
                .distinct()
            )
        ).all()
        return [(client_id, token) for client_id, token in rows]

    async def mark_started(self, client_id: str, match_id: str) -> bool:
        """Record one start attempt per client and match.

        :param client_id: Client UUID.
        :param match_id: Match identifier.
        :return: True if the start was newly recorded.
        """
        exists_row = await self._session.scalar(
            select(LiveActivityStart.id).where(
                LiveActivityStart.client_id == client_id,
                LiveActivityStart.match_id == match_id,
            )
        )
        if exists_row is not None:
            return False
        self._session.add(LiveActivityStart(client_id=client_id, match_id=match_id))
        return True

    async def active_player_ids(self, player_ids: list[str]) -> list[str]:
        """Find participating players with any stored favorite.

        :param player_ids: Participating player identifiers.
        :return: Favorited player IDs.
        """
        if not player_ids:
            return []
        rows = await self._session.scalars(
            select(Favorite.entity_id)
            .where(Favorite.entity_type == FavoriteType.PLAYER.value, Favorite.entity_id.in_(player_ids))
            .distinct()
        )
        return sorted(rows, key=int)

    async def get_match(self, match_id: str) -> MatchPushState | None:
        """Read a match's broadcast channel and last state.

        :param match_id: Match identifier.
        :return: Stored push state, or None.
        """
        return await self._session.get(MatchPushState, match_id)

    async def list_match_ids(self) -> list[str]:
        """List match IDs with stored push state.

        :return: Stored match identifiers.
        """
        return list(await self._session.scalars(select(MatchPushState.match_id)))

    async def save_match(self, match_id: str, channel_id: str | None, last_state_json: str | None) -> None:
        """Save a match channel and its last sent score state.

        :param match_id: Match identifier.
        :param channel_id: APNs broadcast channel, if created.
        :param last_state_json: Last sent compact score JSON, if any.
        :return: None.
        """
        row = await self._session.get(MatchPushState, match_id)
        if row is None:
            self._session.add(MatchPushState(match_id=match_id, channel_id=channel_id, last_state_json=last_state_json))
        else:
            row.channel_id = channel_id
            row.last_state_json = last_state_json

    async def delete_match(self, match_id: str) -> None:
        """Remove a finished match and its client start records.

        :param match_id: Match identifier.
        :return: None.
        """
        await self._session.execute(delete(LiveActivityStart).where(LiveActivityStart.match_id == match_id))
        row = await self._session.get(MatchPushState, match_id)
        if row is not None:
            await self._session.delete(row)
