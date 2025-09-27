from redis import Redis
from utlis.config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_DB,
    REDIS_USERNAME,
    REDIS_PASSWORD,
)


def get_db():
    return Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        username=REDIS_USERNAME,
        password=REDIS_PASSWORD,
    )
