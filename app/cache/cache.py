import redis.asyncio as redis

from ..core.config import settings
from ..core.connections import redis_pool


def get_client() -> redis.Redis:
    """
    Function to get a redis client

    :return: The redis client object
    """
    return redis.Redis(connection_pool=redis_pool)


async def get(key: str, client: redis.Redis | None = None) -> str | None:
    """
    Function to get a value from the cache

    :param key: The key to retrieve
    :param client: A pre-existing redis client
    :return: The value from redis
    """
    if not settings.ENABLE_CACHE:
        return None

    if need_client := client is None:
        client = get_client()
    try:
        return await client.get(key)  # type: ignore
    finally:
        if need_client:
            await client.aclose()


async def set(key: str, value: str, ttl: int = 60, client: redis.Redis | None = None) -> None:
    """
    Function to set a value in the cache

    :param key: The key to set in redis
    :param value: The value to set in redis
    :param ttl: The number of seconds before the item should expire
    :param client: A pre-existing redis client
    :return: Nothing
    """
    if not settings.ENABLE_CACHE:
        return None

    if need_client := client is None:
        client = get_client()
    try:
        return await client.set(key, value, ttl)  # type: ignore
    finally:
        if need_client:
            await client.aclose()


async def hset(name: str, mapping: dict, client: redis.Redis | None = None) -> int | None:
    """
    Function to set a value in the cache

    :param name: The hash name
    :param mapping: The mapping to set in redis
    :param client: A pre-existing redis client
    :return: Nothing
    """
    if not settings.ENABLE_CACHE:
        return None

    if need_client := client is None:
        client = get_client()
    try:
        return await client.hset(name, mapping=mapping)  # type: ignore
    finally:
        if need_client:
            await client.aclose()


async def hget(name: str, key: str, client: redis.Redis | None = None) -> str | None:
    """
    Function to get a value from the cache

    :param name: The hash name
    :param key: The key to retrieve
    :param client: A pre-existing redis client
    :return: The value from redis
    """
    if not settings.ENABLE_CACHE:
        return None

    if need_client := client is None:
        client = get_client()
    try:
        return await client.hget(name, key)  # type: ignore
    finally:
        if need_client:
            await client.aclose()


async def hmget(name: str, keys: list[str], client: redis.Redis | None = None) -> list | None:
    """
    Function to get a value from the cache

    :param name: The hash name
    :param keys: The keys to retrieve
    :param client: A pre-existing redis client
    :return: The value from redis
    """
    if not settings.ENABLE_CACHE:
        return None

    if need_client := client is None:
        client = get_client()
    try:
        return await client.hmget(name, keys)  # type: ignore
    finally:
        if need_client:
            await client.aclose()
