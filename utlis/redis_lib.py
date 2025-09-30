from redis.asyncio import Redis

from utlis.config import (
    REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    REDIS_USERNAME,
)


def get_redis_connection() -> Redis:
    """获取 Redis 异步连接"""
    return Redis(
        host=REDIS_HOST,
        port=int(REDIS_PORT),
        db=int(REDIS_DB),
        username=REDIS_USERNAME if REDIS_USERNAME else None,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        decode_responses=True,
    )
