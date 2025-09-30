"""
清空所有翻译缓存
"""

import asyncio
from services.cache_service import TranslationCache


async def clear_all_cache():
    """清空所有翻译缓存"""
    print("=" * 60)
    print("清空翻译缓存")
    print("=" * 60)

    cache_service = TranslationCache()

    # 查找所有缓存键
    cursor = 0
    deleted_count = 0

    print("\n正在扫描缓存键...")

    while True:
        # 扫描以 translation: 开头的键
        cursor, keys = await cache_service.redis.scan(
            cursor, match="translation:*", count=100
        )

        if keys:
            # 删除这些键
            await cache_service.redis.delete(*keys)
            deleted_count += len(keys)
            print(f"已删除 {len(keys)} 个缓存键...")

        if cursor == 0:
            break

    print(f"\n✓ 清空完成，共删除 {deleted_count} 个缓存键")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(clear_all_cache())
