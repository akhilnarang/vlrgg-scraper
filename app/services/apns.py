"""Minimal APNs client for Live Activity starts and broadcasts."""

import json
import logging
import time
from pathlib import Path
from typing import Literal

import httpx2
import jwt
from pydantic import BaseModel

from app import constants
from app.core.config import settings
from app.exceptions import ServiceUnavailableError
from app.schemas.matches import CompactState
from app.services import push
from app.services.subscription_store import SubscriptionStore

logger = logging.getLogger(__name__)


class APNsCredentials(BaseModel):
    """APNs key and application settings loaded from a credential file."""

    environment: Literal["sandbox", "production"] = "sandbox"
    team_id: str
    key_id: str
    bundle_id: str
    private_key_path: str


class APNsError(Exception):
    """APNs rejection with its status and reason."""

    def __init__(self, status: int, reason: str):
        """Record the failed APNs response.

        :param status: HTTP status code.
        :param reason: APNs error reason.
        :return: None.
        """
        super().__init__(f"APNs request failed ({status}): {reason}")
        self.status = status
        self.reason = reason


async def deliver_start(
    client_id: str,
    token: str,
    match_id: str,
    state: CompactState,
    channel_id: str | None,
) -> None:
    """Deliver an immediate live-match start to an iOS device.

    :param client_id: Client UUID.
    :param token: APNs push-to-start token.
    :param match_id: Match identifier.
    :param state: Current compact match state.
    :param channel_id: Existing APNs broadcast channel, if any.
    :return: None.
    :raises ServiceUnavailableError: If APNs is unavailable or rejects the start.
    """
    from app.core import connections

    client = connections.apns_client
    if client is None:
        raise ServiceUnavailableError("APNs client is not configured")
    sessions = connections.subscription_sessions
    if sessions is None:
        raise ServiceUnavailableError("Live updates are unavailable")

    created_channel = channel_id is None
    if created_channel:
        try:
            channel_id = await client.create_channel()
        except APNsError as exc:
            logger.warning("APNs channel creation failed for match %s: %s", match_id, exc.reason)
            raise ServiceUnavailableError(f"APNs channel creation failed: {exc.reason}") from exc
    async with sessions.begin() as session:
        store = SubscriptionStore(session)
        if created_channel:
            await store.save_match(match_id, channel_id, state.semantic())
        await store.mark_started(client_id, match_id)
    try:
        await client.send_start(token, channel_id, state)
    except APNsError as exc:
        logger.warning("APNs start failed for client %s, match %s: %s", client_id, match_id, exc.reason)
        if exc.reason in constants.DEAD_TOKEN_REASONS:
            await push.clear_rejected_token(token)
        raise ServiceUnavailableError(f"APNs start failed: {exc.reason}") from exc


def load_credentials() -> APNsCredentials | None:
    """Load APNs credentials if a file is configured.

    :return: Credentials with an absolute key path, or None.
    """
    if not settings.APNS_CREDENTIALS_FILE:
        return None
    path = Path(settings.APNS_CREDENTIALS_FILE)
    credentials = APNsCredentials.model_validate(json.loads(path.read_text()))
    key_path = Path(credentials.private_key_path)
    if not key_path.is_absolute():
        key_path = path.parent / key_path
    return credentials.model_copy(update={"private_key_path": str(key_path)})


class APNsClient:
    """Send Live Activity starts and broadcast updates through APNs."""

    def __init__(self, credentials: APNsCredentials, transport=None):
        """Create an HTTP/2 client for the configured APNs environment.

        :param credentials: APNs key and application settings.
        :param transport: Optional HTTP transport for testing.
        :return: None.
        """
        self.credentials = credentials
        self.client = httpx2.AsyncClient(http2=True, transport=transport, timeout=10)
        self.token: str | None = None
        self.token_time = 0.0

    def _jwt(self) -> str:
        """Get a cached or freshly signed APNs authorization token.

        :return: Signed APNs JWT.
        """
        now = time.monotonic()
        if self.token and now - self.token_time < 3000:
            return self.token
        key = Path(self.credentials.private_key_path).read_text()
        self.token = jwt.encode(
            {"iss": self.credentials.team_id, "iat": int(time.time())},
            key,
            algorithm="ES256",
            headers={"kid": self.credentials.key_id},
        )
        self.token_time = now
        return self.token

    @property
    def send_host(self) -> str:
        """Return the environment's APNs delivery host.

        :return: Delivery base URL.
        """
        return (
            "https://api.push.apple.com"
            if self.credentials.environment == "production"
            else "https://api.sandbox.push.apple.com"
        )

    @property
    def manage_host(self) -> str:
        """Return the environment's broadcast management host.

        :return: Management base URL.
        """
        return (
            "https://api-manage-broadcast.push.apple.com:2196"
            if self.credentials.environment == "production"
            else "https://api-manage-broadcast.sandbox.push.apple.com:2195"
        )

    def _headers(self, **values: str) -> dict[str, str]:
        """Add APNs authorization to request headers.

        :param values: Additional APNs headers.
        :return: Complete request headers.
        """
        return {"authorization": f"bearer {self._jwt()}", **values}

    @staticmethod
    def _check(response: httpx2.Response, expected: tuple[int, ...]) -> None:
        """Reject unexpected APNs HTTP responses.

        :param response: HTTP response from APNs.
        :param expected: Accepted status codes.
        :return: None.
        :raises APNsError: If the status is not accepted.
        """
        if response.status_code in expected:
            return
        try:
            reason = response.json().get("reason", "unexpected response")
        except ValueError:
            reason = "unexpected response"
        raise APNsError(response.status_code, reason)

    async def create_channel(self) -> str:
        """Create an APNs Live Activity broadcast channel.

        :return: APNs channel identifier.
        :raises APNsError: If APNs rejects the request or omits the channel ID.
        """
        response = await self.client.post(
            f"{self.manage_host}/1/apps/{self.credentials.bundle_id}/channels",
            headers=self._headers(),
            json={"message-storage-policy": 0, "push-type": "LiveActivity"},
        )
        self._check(response, (201,))
        channel = response.headers.get("apns-channel-id")
        if not channel:
            raise APNsError(response.status_code, "missing apns-channel-id")
        return channel

    async def send_start(self, token: str, channel: str, state: CompactState) -> None:
        """Send a push-to-start request for a matching client.

        :param token: Client push-to-start device token.
        :param channel: Broadcast channel identifier.
        :param state: Initial compact match state.
        :return: None.
        :raises APNsError: If APNs rejects the start.
        """
        names = [team.name for team in state.teams]
        response = await self.client.post(
            f"{self.send_host}/3/device/{token}",
            headers=self._headers(
                **{
                    "apns-push-type": "liveactivity",
                    "apns-topic": f"{self.credentials.bundle_id}.push-type.liveactivity",
                    "apns-priority": "10",
                    "apns-expiration": "0",
                }
            ),
            json={
                "aps": {
                    "timestamp": state.observed_at,
                    "event": "start",
                    "attributes-type": constants.ACTIVITY_ATTRIBUTES_TYPE,
                    "attributes": {"match_id": state.match_id},
                    "content-state": state.model_dump(mode="json"),
                    "input-push-channel": channel,
                    "alert": {"title": f"{names[0]} vs {names[1]}", "body": "Match is live"},
                }
            },
        )
        self._check(response, (200,))

    async def publish(self, channel: str, state: CompactState, terminal: bool) -> None:
        """Broadcast a Live Activity update or end event.

        :param channel: Broadcast channel identifier.
        :param state: Compact match state.
        :param terminal: Whether to end the activity.
        :return: None.
        :raises APNsError: If APNs rejects the broadcast.
        """
        response = await self.client.post(
            f"{self.send_host}/4/broadcasts/apps/{self.credentials.bundle_id}",
            headers=self._headers(
                **{
                    "apns-push-type": "liveactivity",
                    "apns-channel-id": channel,
                    "apns-priority": "10" if terminal else "5",
                    "apns-expiration": "0",
                }
            ),
            json={
                "aps": {
                    "timestamp": state.observed_at,
                    "event": "end" if terminal else "update",
                    "content-state": state.model_dump(mode="json"),
                }
            },
        )
        self._check(response, (200,))

    async def delete_channel(self, channel: str) -> None:
        """Delete a completed APNs broadcast channel.

        :param channel: Broadcast channel identifier.
        :return: None.
        :raises APNsError: If APNs rejects the deletion.
        """
        response = await self.client.delete(
            f"{self.manage_host}/1/apps/{self.credentials.bundle_id}/channels",
            headers=self._headers(**{"apns-channel-id": channel}),
        )
        self._check(response, (200, 204))

    async def aclose(self) -> None:
        """Close the APNs HTTP client.

        :return: None.
        """
        await self.client.aclose()


def build_client(transport=None) -> APNsClient | None:
    """Build an APNs client if credentials are configured.

    :param transport: Optional HTTP transport for testing.
    :return: APNs client, or None without credentials.
    """
    credentials = load_credentials()
    return APNsClient(credentials, transport=transport) if credentials else None
