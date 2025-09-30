from redis.asyncio import Redis

from utlis.config import (
    REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    REDIS_USERNAME,
)


async def get_db() -> Redis:
    if REDIS_HOST is None or REDIS_PORT is None or REDIS_DB is None:
        raise ValueError("Redis config is not set.")

    return await Redis(
        host=REDIS_HOST,
        port=int(REDIS_PORT),
        db=int(REDIS_DB),
        username=REDIS_USERNAME,
        password=REDIS_PASSWORD,
    )
