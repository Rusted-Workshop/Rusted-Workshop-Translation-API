import asyncio
import functools
import random


def retry_async(
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    exceptions: tuple = (Exception,),
    jitter: bool = True,
):
    """
    指数退避重试装饰器

    参数:
        retries: 最大重试次数
        base_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        exceptions: 需要捕获的异常类型
        jitter: 是否增加抖动，防止雪崩
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0
            delay = base_delay
            while attempt < retries:
                try:
                    return await func(*args, **kwargs)
                except exceptions:
                    attempt += 1
                    if attempt >= retries:
                        raise
                    sleep_time = min(delay, max_delay)
                    if jitter:
                        sleep_time = sleep_time * (0.5 + random.random() / 2)
                    await asyncio.sleep(sleep_time)
                    delay *= 2

        return wrapper

    return decorator
