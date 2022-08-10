import redis.asyncio as redis

from app.core.config import settings

from .exceptions import CacheMiss


def get_client() -> redis.Redis:
    """
    Function to get a redis client

    :return: The redis client object
    """
    return redis.Redis(host=settings.REDIS_HOST, password=settings.REDIS_PASSWORD)


async def get(key: str) -> str:
    """
    Function to get a value from the cache

    :param key: The key to retrieve
    :return: The value from redis
    """
    if data := await get_client().get(key):
        return data
    raise CacheMiss(f"{key} not found")


async def set(key: str, value: str, ttl: int = 60) -> None:
    """
    Function to set a value in the cache

    :param key: The key to set in redis
    :param value: The value to set in redis
    :param ttl: The number of seconds before the item should expire
    :return: Nothing
    """
    await get_client().set(key, value, ttl)
