import redis.asyncio as redis

from ..core.connections import redis_pool
from .exceptions import CacheMiss


def get_client() -> redis.Redis:
    """
    Function to get a redis client

    :return: The redis client object
    """
    if redis_pool:
        return redis.Redis(connection_pool=redis_pool)
    raise CacheMiss("Redis not setup")


async def get(key: str) -> str:
    """
    Function to get a value from the cache

    :param key: The key to retrieve
    :return: The value from redis
    """
    client = get_client()
    try:
        if data := await client.get(key):
            return data
        raise CacheMiss(f"`{key}` not found")
    finally:
        await client.close()


async def set(key: str, value: str, ttl: int = 60) -> None:
    """
    Function to set a value in the cache

    :param key: The key to set in redis
    :param value: The value to set in redis
    :param ttl: The number of seconds before the item should expire
    :return: Nothing
    """
    client = get_client()
    try:
        await client.set(key, value, ttl)
    finally:
        await client.close()
