"""
测试 Coordinator Worker Redis 修复

验证 coordinator 可以在新的 event loop 中正确读取文件任务状态
"""

import asyncio

from models.file_task import FileTaskStatus
from services.cache_service import TranslationCache


async def simulate_file_worker(task_id: str, file_id: str, delay: float = 0.1):
    """模拟文件 worker 写入状态"""
    cache = TranslationCache()
    try:
        # 写入 TRANSLATING 状态
        status_key = f"file_task:{task_id}:{file_id}:status"
        await cache.redis.set(status_key, FileTaskStatus.TRANSLATING.value, ex=60)
        await asyncio.sleep(delay)

        # 写入 COMPLETED 状态
        await cache.redis.set(status_key, FileTaskStatus.COMPLETED.value, ex=60)
        print(f"  File worker 完成: {file_id}")
    finally:
        await cache.redis.aclose()


async def simulate_coordinator_check(task_id: str, file_ids: list[str]):
    """模拟 coordinator 检查所有文件状态"""
    # 在新的 event loop 中创建 Redis 连接
    cache_service = TranslationCache()

    try:
        completed_count = 0
        total_files = len(file_ids)

        # 检查所有文件状态
        for file_id in file_ids:
            status_key = f"file_task:{task_id}:{file_id}:status"
            status = await cache_service.redis.get(status_key)

            if status:
                if isinstance(status, bytes):
                    status = status.decode("utf-8")

                if status == FileTaskStatus.COMPLETED.value:
                    completed_count += 1

        print(f"Coordinator 检查结果: {completed_count}/{total_files} 文件完成")
        return completed_count == total_files

    finally:
        await cache_service.redis.aclose()


def test_coordinator_redis():
    """测试 coordinator 在新 event loop 中读取 Redis"""
    task_id = "test_task_123"
    file_ids = [f"file_{i}" for i in range(5)]

    print("=" * 60)
    print("测试：Coordinator 在新 event loop 中读取 Redis 状态")
    print("=" * 60)

    # 步骤 1: 模拟 file workers 写入状态（使用第一个 event loop）
    print("\n[步骤 1] 模拟 file workers 写入状态...")

    async def write_all_files():
        await asyncio.gather(
            *[simulate_file_worker(task_id, file_id) for file_id in file_ids]
        )

    asyncio.run(write_all_files())

    # 步骤 2: 模拟 coordinator 检查状态（使用新的 event loop）
    print("\n[步骤 2] Coordinator 检查状态（新 event loop）...")
    success = asyncio.run(simulate_coordinator_check(task_id, file_ids))

    # 步骤 3: 再次检查（又一个新 event loop）
    print("\n[步骤 3] Coordinator 再次检查（又一个新 event loop）...")
    success2 = asyncio.run(simulate_coordinator_check(task_id, file_ids))

    print("\n" + "=" * 60)
    if success and success2:
        print("✓ 测试通过！Coordinator 可以跨 event loop 读取 Redis")
        return 0
    else:
        print("✗ 测试失败！Coordinator 无法正确读取状态")
        return 1


def test_multiple_tasks():
    """测试处理多个任务（模拟真实场景）"""
    print("\n" + "=" * 60)
    print("测试：处理 3 个翻译任务（模拟真实场景）")
    print("=" * 60)

    for task_num in range(1, 4):
        task_id = f"task_{task_num}"
        file_ids = [f"file_{i}" for i in range(3)]

        print(f"\n--- 任务 {task_num} ---")

        # 写入状态
        async def write_files():
            await asyncio.gather(
                *[simulate_file_worker(task_id, fid, 0.05) for fid in file_ids]
            )

        asyncio.run(write_files())

        # 检查状态
        success = asyncio.run(simulate_coordinator_check(task_id, file_ids))
        if not success:
            print(f"✗ 任务 {task_num} 失败")
            return 1

    print("\n" + "=" * 60)
    print("✓ 测试通过！所有任务处理成功")
    return 0


if __name__ == "__main__":
    result1 = test_coordinator_redis()
    result2 = test_multiple_tasks()

    if result1 == 0 and result2 == 0:
        print("\n" + "=" * 60)
        print("🎉 所有测试通过！")
        print("=" * 60)
        exit(0)
    else:
        exit(1)
