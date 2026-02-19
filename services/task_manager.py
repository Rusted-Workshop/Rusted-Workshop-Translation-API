"""
任务状态管理服务（Postgres 唯一存储）
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from models.task import TaskStatus, TranslationTask
from utils.config import DATABASE_URL

_UNSET = object()

ALLOWED_STATUS_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.PREPARING, TaskStatus.FAILED},
    TaskStatus.PREPARING: {TaskStatus.TRANSLATING, TaskStatus.FAILED},
    TaskStatus.TRANSLATING: {TaskStatus.FINALIZING, TaskStatus.FAILED},
    TaskStatus.FINALIZING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.PENDING},
    TaskStatus.COMPLETED: set(),
}


class InvalidTaskStateTransition(ValueError):
    """非法任务状态迁移"""


class TaskManager:
    """任务管理器 - 使用 Postgres 存储任务状态"""

    def __init__(self, database_url: str = DATABASE_URL):
        self.database_url = database_url

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.database_url)

    @staticmethod
    def _parse_task_id(task_id: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(task_id)
        except (TypeError, ValueError, AttributeError):
            return None

    async def _ensure_schema(self, conn: asyncpg.Connection) -> None:
        # 保持 schema 初始化内聚，避免首次部署遗漏迁移导致服务不可用
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_tasks (
                task_id UUID PRIMARY KEY,
                s3_source_url TEXT NOT NULL,
                s3_dest_bucket TEXT NOT NULL,
                s3_dest_key TEXT NOT NULL,
                target_language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
                translate_style VARCHAR(64) NOT NULL DEFAULT 'auto',
                status VARCHAR(32) NOT NULL,
                progress DOUBLE PRECISION NOT NULL DEFAULT 0,
                total_files INT NOT NULL DEFAULT 0,
                processed_files INT NOT NULL DEFAULT 0,
                error_message TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_translation_tasks_created_at
            ON translation_tasks (created_at DESC)
            """
        )

    @staticmethod
    def _validate_transition(current: TaskStatus, target: TaskStatus) -> None:
        if current == target:
            return
        allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTaskStateTransition(
                f"Invalid status transition: {current.value} -> {target.value}"
            )

    @staticmethod
    def _row_to_task(row: asyncpg.Record) -> TranslationTask:
        return TranslationTask(
            task_id=str(row["task_id"]),
            s3_source_url=row["s3_source_url"],
            s3_dest_bucket=row["s3_dest_bucket"],
            s3_dest_key=row["s3_dest_key"],
            target_language=row["target_language"],
            translate_style=row["translate_style"],
            status=TaskStatus(row["status"]),
            progress=float(row["progress"]),
            total_files=int(row["total_files"]),
            processed_files=int(row["processed_files"]),
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    async def create_task(self, task: TranslationTask) -> TranslationTask:
        """创建新任务"""
        conn = await self._connect()
        try:
            await self._ensure_schema(conn)
            row = await conn.fetchrow(
                """
                INSERT INTO translation_tasks (
                    task_id, s3_source_url, s3_dest_bucket, s3_dest_key,
                    target_language, translate_style, status, progress,
                    total_files, processed_files, error_message
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (task_id)
                DO UPDATE SET
                    s3_source_url = EXCLUDED.s3_source_url,
                    s3_dest_bucket = EXCLUDED.s3_dest_bucket,
                    s3_dest_key = EXCLUDED.s3_dest_key,
                    target_language = EXCLUDED.target_language,
                    translate_style = EXCLUDED.translate_style,
                    status = EXCLUDED.status,
                    progress = EXCLUDED.progress,
                    total_files = EXCLUDED.total_files,
                    processed_files = EXCLUDED.processed_files,
                    error_message = EXCLUDED.error_message,
                    updated_at = NOW()
                RETURNING *
                """,
                uuid.UUID(task.task_id),
                task.s3_source_url,
                task.s3_dest_bucket,
                task.s3_dest_key,
                task.target_language,
                task.translate_style,
                task.status.value,
                float(task.progress),
                int(task.total_files),
                int(task.processed_files),
                task.error_message,
            )
            if not row:
                raise RuntimeError("Failed to insert task")
            return self._row_to_task(row)
        finally:
            await conn.close()

    async def get_task(self, task_id: str) -> Optional[TranslationTask]:
        """获取任务详情"""
        task_uuid = self._parse_task_id(task_id)
        if not task_uuid:
            return None

        conn = await self._connect()
        try:
            await self._ensure_schema(conn)
            row = await conn.fetchrow(
                "SELECT * FROM translation_tasks WHERE task_id = $1",
                task_uuid,
            )
            if not row:
                return None
            return self._row_to_task(row)
        finally:
            await conn.close()

    async def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[float] = None,
        total_files: Optional[int] = None,
        processed_files: Optional[int] = None,
        error_message: str | None | object = _UNSET,
    ) -> Optional[TranslationTask]:
        """更新任务状态"""
        task_uuid = self._parse_task_id(task_id)
        if not task_uuid:
            return None

        conn = await self._connect()
        try:
            await self._ensure_schema(conn)
            async with conn.transaction():
                current = await conn.fetchrow(
                    "SELECT * FROM translation_tasks WHERE task_id = $1 FOR UPDATE",
                    task_uuid,
                )
                if not current:
                    return None

                current_status = TaskStatus(current["status"])
                next_status = status or current_status
                self._validate_transition(current_status, next_status)

                new_progress = (
                    float(progress) if progress is not None else current["progress"]
                )
                new_total_files = (
                    int(total_files)
                    if total_files is not None
                    else current["total_files"]
                )
                new_processed_files = (
                    int(processed_files)
                    if processed_files is not None
                    else current["processed_files"]
                )
                new_error_message = (
                    current["error_message"]
                    if error_message is _UNSET
                    else error_message
                )
                completed_at = current["completed_at"]

                if next_status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                    completed_at = completed_at or datetime.now(timezone.utc)
                else:
                    completed_at = None

                row = await conn.fetchrow(
                    """
                    UPDATE translation_tasks
                    SET
                        status = $2,
                        progress = $3,
                        total_files = $4,
                        processed_files = $5,
                        error_message = $6,
                        updated_at = NOW(),
                        completed_at = $7
                    WHERE task_id = $1
                    RETURNING *
                    """,
                    task_uuid,
                    next_status.value,
                    new_progress,
                    new_total_files,
                    new_processed_files,
                    new_error_message,
                    completed_at,
                )
                if not row:
                    return None
                return self._row_to_task(row)
        finally:
            await conn.close()

    async def list_tasks(
        self, limit: int = 50, offset: int = 0
    ) -> list[TranslationTask]:
        """列出所有任务（按创建时间倒序）"""
        conn = await self._connect()
        try:
            await self._ensure_schema(conn)
            rows = await conn.fetch(
                """
                SELECT *
                FROM translation_tasks
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                int(limit),
                int(offset),
            )
            return [self._row_to_task(row) for row in rows]
        finally:
            await conn.close()

    async def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        task_uuid = self._parse_task_id(task_id)
        if not task_uuid:
            return False

        conn = await self._connect()
        try:
            await self._ensure_schema(conn)
            result = await conn.execute(
                "DELETE FROM translation_tasks WHERE task_id = $1",
                task_uuid,
            )
            return result.endswith("1")
        finally:
            await conn.close()

