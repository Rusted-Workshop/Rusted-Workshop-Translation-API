"""
定时清理任务文件 Worker
"""

import os
import shutil
import time
from datetime import datetime, timedelta

from services.task_manager import TaskManager
from models.task import TaskStatus


class CleanupWorker:
    """清理过期任务文件的 Worker"""

    def __init__(
        self,
        work_dir: str = "/tmp/translation_work",
        retention_days: int = 7,
        check_interval: int = 3600,  # 1小时检查一次
    ):
        """
        初始化清理 Worker

        Args:
            work_dir: 工作目录路径
            retention_days: 保留天数（超过此天数的已完成任务将被清理）
            check_interval: 检查间隔（秒）
        """
        self.work_dir = work_dir
        self.retention_days = retention_days
        self.check_interval = check_interval
        self.task_manager = TaskManager()

    def start(self):
        """启动清理 Worker"""
        import asyncio

        print("Cleanup Worker 启动中...")
        print(f"工作目录: {self.work_dir}")
        print(f"保留天数: {self.retention_days}")
        print(f"检查间隔: {self.check_interval}秒")

        while True:
            try:
                asyncio.run(self.cleanup_old_tasks())
                self.cleanup_orphaned_files()
            except Exception as e:
                print(f"清理任务失败: {e}")

            # 等待下一次检查
            time.sleep(self.check_interval)

    async def cleanup_old_tasks(self):
        """清理过期的已完成任务"""
        print(f"[{datetime.now()}] 开始清理过期任务")

        # 计算截止时间
        cutoff_time = datetime.now() - timedelta(days=self.retention_days)

        # 获取所有任务
        tasks = await self.task_manager.list_tasks(limit=1000)

        deleted_count = 0
        for task in tasks:
            # 只清理已完成或失败的任务
            if task.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                continue

            # 检查是否过期
            if task.completed_at and task.completed_at < cutoff_time:
                print(f"  删除过期任务: {task.task_id} (完成时间: {task.completed_at})")
                await self.task_manager.delete_task(task.task_id)
                deleted_count += 1

        print(f"清理完成，共删除 {deleted_count} 个过期任务")

    def cleanup_orphaned_files(self):
        """清理孤立的临时文件"""
        if not os.path.exists(self.work_dir):
            return

        print(f"[{datetime.now()}] 开始清理孤立文件")

        deleted_count = 0
        cutoff_time = time.time() - (self.retention_days * 24 * 3600)

        for item in os.listdir(self.work_dir):
            item_path = os.path.join(self.work_dir, item)

            # 检查文件修改时间
            if os.path.getmtime(item_path) < cutoff_time:
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    print(f"  删除孤立文件: {item}")
                    deleted_count += 1
                except Exception as e:
                    print(f"  删除文件失败 {item}: {e}")

        print(f"清理完成，共删除 {deleted_count} 个孤立文件")


if __name__ == "__main__":
    # 从环境变量读取配置
    work_dir = os.getenv("WORK_DIR", "/tmp/translation_work")
    retention_days = int(os.getenv("RETENTION_DAYS", "7"))
    check_interval = int(os.getenv("CHECK_INTERVAL", "3600"))

    worker = CleanupWorker(
        work_dir=work_dir,
        retention_days=retention_days,
        check_interval=check_interval,
    )
    worker.start()
