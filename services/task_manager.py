"""
任务状态管理服务
"""

from datetime import datetime
from typing import Optional

from models.task import TaskStatus, TranslationTask
from utlis.redis_lib import get_redis_connection


class TaskManager:
    """任务管理器 - 使用Redis存储任务状态"""

    def __init__(self):
        self.redis = get_redis_connection()
        self.task_key_prefix = "translation_task:"
        self.task_list_key = "translation_tasks"

    def _get_task_key(self, task_id: str) -> str:
        """获取任务的Redis键"""
        return f"{self.task_key_prefix}{task_id}"

    async def create_task(self, task: TranslationTask) -> TranslationTask:
        """创建新任务"""
        task_key = self._get_task_key(task.task_id)
        task_data = task.model_dump_json()

        # 存储任务数据
        await self.redis.set(task_key, task_data)
        # 添加到任务列表
        await self.redis.zadd(
            self.task_list_key, {task.task_id: task.created_at.timestamp()}
        )
        # 设置过期时间（7天）
        await self.redis.expire(task_key, 7 * 24 * 60 * 60)

        return task

    async def get_task(self, task_id: str) -> Optional[TranslationTask]:
        """获取任务详情"""
        task_key = self._get_task_key(task_id)
        task_data = await self.redis.get(task_key)

        if not task_data:
            return None

        return TranslationTask.model_validate_json(task_data)

    async def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[float] = None,
        current_file: Optional[str] = None,
        total_files: Optional[int] = None,
        processed_files: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> Optional[TranslationTask]:
        """更新任务状态"""
        task = await self.get_task(task_id)
        if not task:
            return None

        # 更新字段
        if status is not None:
            task.status = status
        if progress is not None:
            task.progress = progress
        if current_file is not None:
            task.current_file = current_file
        if total_files is not None:
            task.total_files = total_files
        if processed_files is not None:
            task.processed_files = processed_files
        if error_message is not None:
            task.error_message = error_message

        task.updated_at = datetime.now()

        # 如果任务完成或失败，记录完成时间
        if status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
            task.completed_at = datetime.now()

        # 保存更新
        task_key = self._get_task_key(task_id)
        await self.redis.set(task_key, task.model_dump_json())

        return task

    async def list_tasks(
        self, limit: int = 50, offset: int = 0
    ) -> list[TranslationTask]:
        """列出所有任务（按创建时间倒序）"""
        task_ids = await self.redis.zrevrange(
            self.task_list_key, offset, offset + limit - 1
        )

        tasks = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task:
                tasks.append(task)

        return tasks

    async def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        task_key = self._get_task_key(task_id)
        result = await self.redis.delete(task_key)
        await self.redis.zrem(self.task_list_key, task_id)
        return result > 0
