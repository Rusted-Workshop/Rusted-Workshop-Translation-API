"""
测试 Event Loop 修复

验证：
1. 每个 asyncio.run() 使用独立的 Redis 连接
2. Redis 连接在 finally 块中正确关闭
3. 不会出现 "Event loop is closed" 错误
"""

import asyncio

from models.file_task import FileTaskStatus
from services.cache_service import TranslationCache


async def test_redis_in_new_loop(iteration: int):
    """模拟在新 event loop 中使用 Redis"""
    print(f"[迭代 {iteration}] 创建新的 Redis 连接")

    # 每次在新的 event loop 中创建 Redis 连接
    cache_service = TranslationCache()

    try:
        # 模拟更新状态
        status_key = f"test_task:file_{iteration}:status"
        await cache_service.redis.set(
            status_key, FileTaskStatus.TRANSLATING.value, ex=60
        )
        print(f"[迭代 {iteration}] ✓ 写入状态: TRANSLATING")

        # 读取状态
        status = await cache_service.redis.get(status_key)
        print(f"[迭代 {iteration}] ✓ 读取状态: {status}")

        # 模拟完成
        await cache_service.redis.set(status_key, FileTaskStatus.COMPLETED.value, ex=60)
        print(f"[迭代 {iteration}] ✓ 更新状态: COMPLETED")

        return True

    except Exception as e:
        print(f"[迭代 {iteration}] ✗ 错误: {e}")
        return False

    finally:
        # 关闭 Redis 连接
        await cache_service.redis.aclose()
        print(f"[迭代 {iteration}] ✓ 关闭 Redis 连接")


def main():
    """主测试函数 - 模拟 RabbitMQ 消息处理"""
    print("=" * 60)
    print("测试：多次 asyncio.run() 调用（模拟消息队列处理）")
    print("=" * 60)

    # 模拟处理 10 条消息，每条消息都用 asyncio.run()
    success_count = 0
    for i in range(1, 11):
        try:
            print(f"\n--- 处理消息 {i} ---")
            # 每次调用 asyncio.run() 都会创建新的 event loop
            result = asyncio.run(test_redis_in_new_loop(i))
            if result:
                success_count += 1
        except Exception as e:
            print(f"[消息 {i}] ✗ 失败: {e}")

    print("\n" + "=" * 60)
    print(f"测试结果: {success_count}/10 成功")
    print("=" * 60)

    if success_count == 10:
        print("\n✓ 测试通过！所有消息处理成功，无 Event loop 错误")
        return 0
    else:
        print(f"\n✗ 测试失败！有 {10 - success_count} 条消息处理失败")
        return 1


if __name__ == "__main__":
    exit(main())
