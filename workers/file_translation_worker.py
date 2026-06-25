"""
文件翻译 Worker

专门负责翻译单文件/文件批次，接收协调器发送的消息
"""

import json
import os
import traceback
from typing import Iterable

from pika.spec import Basic, BasicProperties

from core.translate import translate_file_preserve_structure, translate_files_batch
from models.file_task import (
    FileBatchTranslationMessage,
    FileTaskStatus,
    FileTranslationMessage,
)
from services.cache_service import TranslationCache
from services.rabbitmq_service import get_rabbitmq_service


class FileTranslationWorker:
    """文件翻译 Worker"""

    QUEUE_NAME = "file_translation_tasks"

    def __init__(self):
        self.rabbitmq = get_rabbitmq_service()

    def start(self):
        """启动 Worker"""
        print("File Translation Worker 启动中...")
        self.rabbitmq.connect()
        self.rabbitmq.declare_queue(self.QUEUE_NAME)
        print(f"开始监听队列: {self.QUEUE_NAME}")

        # 增加并发数：每个 worker 可以同时处理多个文件
        prefetch_count = int(os.getenv("FILE_WORKER_PREFETCH", "3"))

        try:
            self.rabbitmq.consume_messages(
                self.QUEUE_NAME, self.process_message, prefetch_count=prefetch_count
            )
        except KeyboardInterrupt:
            print("File Translation Worker 收到停止信号，准备退出...")
        finally:
            self.rabbitmq.close()

    def process_message(
        self,
        channel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ):
        """处理消息"""
        import asyncio

        message_data: dict = {}
        task_id = None
        batch_label = None

        try:
            message_data = json.loads(body)

            # 区分单文件 / 批量消息
            if "file_ids" in message_data and "file_paths" in message_data:
                message = FileBatchTranslationMessage(**message_data)
                task_id = message.task_id
                batch_label = f"{len(message.file_ids)} files"
                print(
                    f"[{task_id}] 收到批量消息: files={len(message.file_paths)} "
                    f"work_dir={message.work_dir}"
                )

                is_stale = asyncio.run(self._is_stale_batch_message(message))
                if is_stale:
                    self.rabbitmq.ack_message(method.delivery_tag)
                    print(
                        f"[{task_id}] 跳过旧批次文件消息 (run_id={message.run_id})"
                    )
                    return

                asyncio.run(self._process_batch_async(message))
                self.rabbitmq.ack_message(method.delivery_tag)
                print(f"[{task_id}] 批量翻译完成: {batch_label}")
            else:
                message = FileTranslationMessage(**message_data)
                task_id = message.task_id
                file_id = message.file_id
                batch_label = f"{file_id}"
                print(f"[{task_id}:{file_id}] 开始翻译文件: {message.file_path}")

                is_stale = asyncio.run(self._is_stale_file_message(message))
                if is_stale:
                    self.rabbitmq.ack_message(method.delivery_tag)
                    print(
                        f"[{task_id}:{file_id}] 跳过旧批次文件消息: {message.file_path} "
                        f"(run_id={message.run_id})"
                    )
                    return

                asyncio.run(self._process_file_async(message))
                self.rabbitmq.ack_message(method.delivery_tag)
                print(f"[{task_id}:{file_id}] 文件翻译完成: {message.file_path}")

        except Exception as e:
            print(f"[{task_id}] 翻译失败 ({batch_label}): {e}")
            traceback.print_exc()

            # 批量消息失败时把所有子项标记为 FAILED
            try:
                if "file_ids" in message_data:
                    asyncio.run(
                        self._mark_batch_failed(message_data, str(e))
                    )
            except Exception:
                pass

            # 拒绝消息，不重新入队
            self.rabbitmq.nack_message(method.delivery_tag, requeue=False)

    async def _mark_batch_failed(self, message_data: dict, error: str) -> None:
        """将批量消息内的全部子项标记为 FAILED。"""
        cache_service = TranslationCache()
        try:
            task_id = message_data.get("task_id")
            file_ids: Iterable[str] = message_data.get("file_ids", [])
            ttl = int(os.getenv("FILE_TASK_STATUS_TTL_SECONDS", "21600"))
            for fid in file_ids:
                await cache_service.redis.set(
                    f"file_task:{task_id}:{fid}:status",
                    FileTaskStatus.FAILED.value,
                    ex=ttl,
                )
                await cache_service.redis.set(
                    f"file_task:{task_id}:{fid}:error",
                    error,
                    ex=ttl,
                )
        finally:
            await cache_service.redis.aclose()

    async def _is_stale_file_message(self, message: FileTranslationMessage) -> bool:
        return await self._is_stale_run_id(message.task_id, message.run_id, message.work_dir)

    async def _is_stale_batch_message(self, message: FileBatchTranslationMessage) -> bool:
        return await self._is_stale_run_id(message.task_id, message.run_id, message.work_dir)

    async def _is_stale_run_id(
        self, task_id: str, run_id: str | None, work_dir: str
    ) -> bool:
        cache_service = TranslationCache()
        try:
            run_key = f"task:{task_id}:run_id"
            current_run_id = await cache_service.redis.get(run_key)
            if not current_run_id:
                if not os.path.exists(work_dir):
                    return True
                return False
            if isinstance(current_run_id, bytes):
                current_run_id = current_run_id.decode("utf-8")
            current_run_id = str(current_run_id).strip()
            message_run_id = str(run_id or "").strip()
            return message_run_id != current_run_id
        finally:
            await cache_service.redis.aclose()

    async def _process_file_async(self, message: FileTranslationMessage):
        """异步处理单文件翻译。"""
        task_id = message.task_id
        file_id = message.file_id

        cache_service = TranslationCache()
        try:
            await self._update_file_task_status(
                task_id, file_id, FileTaskStatus.TRANSLATING, cache_service
            )
            await self._translate_file(message)
            await self._update_file_task_status(
                task_id, file_id, FileTaskStatus.COMPLETED, cache_service
            )
        except Exception as e:
            await self._update_file_task_status(
                task_id, file_id, FileTaskStatus.FAILED, cache_service, str(e)
            )
            raise
        finally:
            await cache_service.redis.aclose()

    async def _process_batch_async(self, message: FileBatchTranslationMessage):
        """异步处理文件批次翻译：所有文件共用一次 LLM 调用。"""
        task_id = message.task_id
        work_dir = message.work_dir
        file_ids = list(message.file_ids)
        file_paths = list(message.file_paths)

        if len(file_ids) != len(file_paths):
            raise ValueError(
                f"file_ids/file_paths 数量不匹配: {len(file_ids)} vs {len(file_paths)}"
            )

        cache_service = TranslationCache()
        try:
            # 全部置为翻译中
            for fid in file_ids:
                await self._update_file_task_status(
                    task_id, fid, FileTaskStatus.TRANSLATING, cache_service
                )

            errors = await translate_files_batch(
                file_paths=file_paths,
                work_dir=work_dir,
                translate_style=message.translate_style,
                target_language=message.target_language,
            )

            # 写回结果：成功的 COMPLETED，失败的 FAILED
            failed_files = {os.path.join(work_dir, p.replace("/", os.sep)): err for p, err in errors.items()}
            for fid, fpath in zip(file_ids, file_paths):
                full_path = os.path.join(work_dir, fpath.replace("/", os.sep))
                if full_path in failed_files:
                    await self._update_file_task_status(
                        task_id,
                        fid,
                        FileTaskStatus.FAILED,
                        cache_service,
                        failed_files[full_path],
                    )
                else:
                    await self._update_file_task_status(
                        task_id, fid, FileTaskStatus.COMPLETED, cache_service
                    )

            if errors:
                # 让上层把第一个错误抛出（与单文件行为一致）
                first_path, first_err = next(iter(errors.items()))
                raise RuntimeError(f"batch 翻译部分失败: {first_path}: {first_err}")
        finally:
            await cache_service.redis.aclose()

    async def _translate_file(self, message: FileTranslationMessage):
        """翻译单个文件"""
        task_id = message.task_id
        file_id = message.file_id
        file_path = message.file_path
        work_dir = message.work_dir
        translate_style = message.translate_style
        target_language = message.target_language

        file_path_normalized = file_path.replace("/", os.sep)
        full_path = os.path.join(work_dir, file_path_normalized)

        print(f"[{task_id}:{file_id}] 完整路径: {full_path}")

        if not os.path.exists(full_path):
            raise FileNotFoundError(
                f"文件不存在: {full_path}\n工作目录: {work_dir}\n相对路径: {file_path}"
            )

        await translate_file_preserve_structure(
            full_path,
            translate_style=translate_style,
            target_language=target_language,
        )

        print(f"[{task_id}:{file_id}] 文件翻译并保存完成: {file_path}")

    async def _update_file_task_status(
        self,
        task_id: str,
        file_id: str,
        status: FileTaskStatus,
        cache_service: TranslationCache,
        error_message: str = "",
    ):
        status_key = f"file_task:{task_id}:{file_id}:status"
        status_ttl_seconds = int(os.getenv("FILE_TASK_STATUS_TTL_SECONDS", "21600"))
        await cache_service.redis.set(status_key, status.value, ex=status_ttl_seconds)

        if error_message:
            error_key = f"file_task:{task_id}:{file_id}:error"
            await cache_service.redis.set(
                error_key, error_message, ex=status_ttl_seconds
            )


if __name__ == "__main__":
    worker = FileTranslationWorker()
    worker.start()

