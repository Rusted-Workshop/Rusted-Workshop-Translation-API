"""
翻译任务协调器 Worker

负责：
1. 接收主任务
2. 下载和解压文件
3. 分析翻译风格
4. 创建文件级子任务并发送到翻译队列
5. 监控所有子任务完成情况
6. 打包和上传结果
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import time
import traceback
import uuid
from typing import Dict

from pika.spec import Basic, BasicProperties

from core.rwmod import RWMod
from models.file_task import FileTaskStatus, FileTranslationMessage, FileTranslationTask
from models.task import TaskStatus, TranslationTask
from services.cache_service import TranslationCache
from services.rabbitmq_service import get_rabbitmq_service
from services.s3_service import S3Service, create_archive, extract_archive
from services.task_manager import TaskManager


class CoordinatorWorker:
    """翻译任务协调器 Worker"""

    MAIN_QUEUE_NAME = "translation_tasks"
    FILE_QUEUE_NAME = "file_translation_tasks"

    def __init__(self):
        self.rabbitmq = get_rabbitmq_service()
        self.task_manager = TaskManager()
        self.s3_service = S3Service()
        self.file_tasks: Dict[str, Dict[str, FileTranslationTask]] = {}

    def start(self):
        """启动协调器 Worker"""
        print("Coordinator Worker 启动中...")
        self.rabbitmq.connect()
        self.rabbitmq.declare_queue(self.MAIN_QUEUE_NAME)
        self.rabbitmq.declare_queue(self.FILE_QUEUE_NAME)
        asyncio.run(self._recover_stale_tasks_on_startup())
        print(f"开始监听队列: {self.MAIN_QUEUE_NAME}")
        try:
            self.rabbitmq.consume_messages(
                self.MAIN_QUEUE_NAME,
                self.process_message,
                prefetch_count=1,
                exclusive=True,
            )
        except KeyboardInterrupt:
            print("Coordinator Worker 收到停止信号，准备退出...")
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
        message: dict = {}
        task_id = None

        try:
            message = json.loads(body)
            task_id = message["task_id"]

            print(f"[{task_id}] 协调器开始处理翻译任务")

            # 执行协调任务
            self._process_coordination_task(message)

            # 确认消息
            self.rabbitmq.ack_message(method.delivery_tag)
            print(f"[{task_id}] 协调器任务处理完成")

        except Exception as e:
            print(f"协调器处理消息失败: {e}")
            traceback.print_exc()
            # 拒绝消息，不重新入队
            self.rabbitmq.nack_message(method.delivery_tag, requeue=False)

            # 更新任务状态为失败
            if task_id:
                try:
                    asyncio.run(
                        self.task_manager.update_task(
                            task_id,
                            status=TaskStatus.FAILED,
                            error_message=str(e),
                        )
                    )
                except Exception as update_error:
                    print(f"更新任务状态失败: {update_error}")
                    traceback.print_exc()

    def _process_coordination_task(self, message: dict):
        """
        处理协调任务

        Args:
            message: 任务消息
        """
        asyncio.run(self._process_coordination_task_async(message))

    def _is_task_stale(self, task: TranslationTask, stale_seconds: int) -> bool:
        updated_at = task.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age >= stale_seconds

    @staticmethod
    def _is_truthy(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    async def _recover_stale_tasks_on_startup(self) -> None:
        recover_on_startup = (
            str(os.getenv("COORDINATOR_RECOVER_ON_STARTUP", "true")).strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if not recover_on_startup:
            return

        stale_seconds = int(os.getenv("COORDINATOR_RECOVER_STALE_SECONDS", "45"))
        recover_all_in_progress = (
            str(os.getenv("COORDINATOR_RECOVER_ALL_IN_PROGRESS_ON_STARTUP", "true"))
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        scan_limit = int(os.getenv("COORDINATOR_RECOVER_SCAN_LIMIT", "200"))
        offset = 0
        recovered = 0

        while True:
            tasks = await self.task_manager.list_tasks(limit=scan_limit, offset=offset)
            if not tasks:
                break

            for task in tasks:
                if task.status not in {
                    TaskStatus.PREPARING,
                    TaskStatus.TRANSLATING,
                    TaskStatus.FINALIZING,
                }:
                    continue
                if (not recover_all_in_progress) and (
                    not self._is_task_stale(task, stale_seconds)
                ):
                    continue

                print(
                    f"[{task.task_id}] 启动恢复僵尸任务: status={task.status.value}, "
                    f"updated_at={task.updated_at.isoformat()}"
                )
                await self.task_manager.update_task(
                    task.task_id,
                    status=TaskStatus.FAILED,
                    error_message="Recovered stale in-progress task on startup",
                )
                await self.task_manager.update_task(
                    task.task_id,
                    status=TaskStatus.PENDING,
                    progress=0.0,
                    total_files=0,
                    processed_files=0,
                    error_message=None,
                )
                self.rabbitmq.publish_message(
                    self.MAIN_QUEUE_NAME,
                    {
                        "task_id": task.task_id,
                        "s3_source_url": task.s3_source_url,
                        "s3_dest_bucket": task.s3_dest_bucket,
                        "s3_dest_key": task.s3_dest_key,
                        "target_language": task.target_language,
                        "force_recover": True,
                    },
                )
                recovered += 1

            if len(tasks) < scan_limit:
                break
            offset += scan_limit

        if recovered > 0:
            print(f"启动恢复完成：已重新入队 {recovered} 个僵尸任务")

    async def _process_coordination_task_async(self, message: dict):
        """
        处理协调任务（异步）

        Args:
            message: 任务消息
        """
        task_id = message["task_id"]
        s3_source_url = message["s3_source_url"]
        s3_dest_bucket = message["s3_dest_bucket"]
        s3_dest_key = message["s3_dest_key"]
        target_language = message.get("target_language", "中文")

        current_task = await self.task_manager.get_task(task_id)
        if not current_task:
            raise ValueError(f"任务不存在: {task_id}")

        # 主任务消息可能因网络重连/历史残留被重复消费，终态任务直接跳过
        if current_task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            print(
                f"[{task_id}] 任务已是终态({current_task.status.value})，"
                "跳过重复消息"
            )
            return

        recover_stale_seconds = int(os.getenv("COORDINATOR_RECOVER_STALE_SECONDS", "45"))
        force_recover = self._is_truthy(message.get("force_recover"))
        if current_task.status != TaskStatus.PENDING:
            if (
                current_task.status
                in {TaskStatus.PREPARING, TaskStatus.TRANSLATING, TaskStatus.FINALIZING}
                and (force_recover or self._is_task_stale(current_task, recover_stale_seconds))
            ):
                updated_at = current_task.updated_at
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                age_seconds = int(
                    (datetime.now(timezone.utc) - updated_at).total_seconds()
                )
                print(
                    f"[{task_id}] 检测到僵尸任务(status={current_task.status.value})，"
                    f"距上次更新 {age_seconds}s，进入恢复流程 "
                    f"(force_recover={force_recover})"
                )
                await self.task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    error_message="Worker interrupted, auto-recover task",
                )
                await self.task_manager.update_task(
                    task_id,
                    status=TaskStatus.PENDING,
                    progress=0.0,
                    total_files=0,
                    processed_files=0,
                    error_message=None,
                )
            else:
                print(
                    f"[{task_id}] 任务当前状态为 {current_task.status.value}，"
                    "跳过重复/并发消息"
                )
                return

        # 重新读取一次，避免并发恢复后状态与预期不一致
        current_task = await self.task_manager.get_task(task_id)
        if not current_task:
            raise ValueError(f"任务不存在: {task_id}")
        if current_task.status != TaskStatus.PENDING:
            print(
                f"[{task_id}] 任务当前状态为 {current_task.status.value}，"
                "跳过重复/并发消息(恢复后状态非 pending)"
            )
            return

        # 在当前 event loop 中创建新的 Redis 连接
        cache_service = TranslationCache()

        # 创建临时工作目录
        work_dir = tempfile.mkdtemp(prefix=f"translation_{task_id}_")

        try:
            # 1. 下载文件
            print(f"[{task_id}] 下载文件: {s3_source_url}")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.PREPARING, progress=5.0
            )

            archive_path = os.path.join(work_dir, "source.rwmod")
            try:
                await self.s3_service.download_file(s3_source_url, archive_path)
            except Exception as e:
                error_msg = f"下载文件失败: {str(e)}"
                print(f"[{task_id}] {error_msg}")
                await self.task_manager.update_task(
                    task_id, status=TaskStatus.FAILED, error_message=error_msg
                )
                raise

            # 2. 解压文件
            print(f"[{task_id}] 解压文件")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.PREPARING, progress=10.0
            )

            extract_dir = os.path.join(work_dir, "extracted")
            try:
                extract_archive(archive_path, extract_dir)
            except Exception as e:
                error_msg = f"解压文件失败: {str(e)}"
                print(f"[{task_id}] {error_msg}")
                await self.task_manager.update_task(
                    task_id, status=TaskStatus.FAILED, error_message=error_msg
                )
                raise

            # 3. 分析模组
            print(f"[{task_id}] 分析模组")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.PREPARING, progress=15.0
            )

            try:
                rwmod = RWMod(extract_dir)

                # 分析风格
                print(f"[{task_id}] 分析翻译风格")
                style = await rwmod.analysis_style()
                rwmod.style = style
            except Exception as e:
                error_msg = f"分析模组失败: {str(e)}"
                print(f"[{task_id}] {error_msg}")
                await self.task_manager.update_task(
                    task_id, status=TaskStatus.FAILED, error_message=error_msg
                )
                raise

            # 4. 创建文件翻译子任务
            print(f"[{task_id}] 创建文件翻译子任务")
            await self.task_manager.update_task(
                task_id,
                status=TaskStatus.TRANSLATING,
                progress=20.0,
                total_files=len(rwmod.unit_datas),
            )

            file_tasks: Dict[str, FileTranslationTask] = {}
            file_status_ttl_seconds = int(
                os.getenv("FILE_TASK_STATUS_TTL_SECONDS", "21600")
            )
            run_id = str(uuid.uuid4())
            task_run_key = f"task:{task_id}:run_id"
            await cache_service.redis.set(task_run_key, run_id, ex=file_status_ttl_seconds)
            print(f"[{task_id}] 当前翻译批次 run_id={run_id}")

            for inifile in rwmod.unit_datas:
                file_id = str(uuid.uuid4())
                # 使用正斜杠路径，跨平台兼容
                file_path = os.path.relpath(inifile.path, extract_dir).replace(
                    "\\", "/"
                )

                file_task = FileTranslationTask(
                    task_id=task_id,
                    file_id=file_id,
                    file_path=file_path,
                    status=FileTaskStatus.PENDING,
                )

                file_tasks[file_id] = file_task

                # 先写入 pending 状态，避免协调器侧统计为 unknown
                status_key = f"file_task:{task_id}:{file_id}:status"
                await cache_service.redis.set(
                    status_key, FileTaskStatus.PENDING.value, ex=file_status_ttl_seconds
                )

                # 发送到文件翻译队列
                file_message = FileTranslationMessage(
                    task_id=task_id,
                    file_id=file_id,
                    file_path=file_path,
                    work_dir=extract_dir,
                    translate_style=style,
                    target_language=target_language,
                    run_id=run_id,
                )

                self.rabbitmq.publish_message(
                    self.FILE_QUEUE_NAME, file_message.model_dump()
                )

            self.file_tasks[task_id] = file_tasks
            print(f"[{task_id}] 已创建 {len(file_tasks)} 个文件翻译子任务")

            # 5. 等待所有文件翻译完成
            print(f"[{task_id}] 等待所有文件翻译完成")
            await self._wait_for_file_tasks(task_id, extract_dir, cache_service)

            # 6. 打包
            print(f"[{task_id}] 打包文件")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.FINALIZING, progress=90.0
            )

            try:
                output_archive = os.path.join(work_dir, "translated.rwmod")
                create_archive(extract_dir, output_archive, format="zip")
            except Exception as e:
                error_msg = f"打包文件失败: {str(e)}"
                print(f"[{task_id}] {error_msg}")
                await self.task_manager.update_task(
                    task_id, status=TaskStatus.FAILED, error_message=error_msg
                )
                raise

            # 7. 上传到S3
            print(f"[{task_id}] 上传到 S3")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.FINALIZING, progress=95.0
            )

            try:
                s3_url = await self.s3_service.upload_file(
                    output_archive, s3_dest_bucket, s3_dest_key
                )
            except Exception as e:
                error_msg = f"上传到S3失败: {str(e)}"
                print(f"[{task_id}] {error_msg}")
                await self.task_manager.update_task(
                    task_id, status=TaskStatus.FAILED, error_message=error_msg
                )
                raise

            # 8. 完成
            print(f"[{task_id}] 任务完成: {s3_url}")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.COMPLETED, progress=100.0
            )

            # 清理文件任务记录
            if task_id in self.file_tasks:
                del self.file_tasks[task_id]

        except Exception:
            raise
        finally:
            # 关闭 Redis 连接
            await cache_service.redis.aclose()

            # 清理临时文件
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
                print(f"[{task_id}] 清理临时文件")

    async def _wait_for_file_tasks(
        self, task_id: str, extract_dir: str, cache_service: TranslationCache
    ):
        """
        等待所有文件翻译任务完成

        Args:
            task_id: 主任务ID
            extract_dir: 解压目录
            cache_service: Redis 缓存服务实例
        """
        file_tasks = self.file_tasks.get(task_id, {})
        total_files = len(file_tasks)
        check_interval = max(
            0.5, float(os.getenv("FILE_TASK_CHECK_INTERVAL_SECONDS", "2"))
        )
        timeout_seconds = max(
            check_interval, float(os.getenv("FILE_TASK_TIMEOUT_SECONDS", "1800"))
        )
        stall_timeout_seconds = max(
            check_interval, float(os.getenv("FILE_TASK_STALL_TIMEOUT_SECONDS", "180"))
        )
        status_log_interval = max(
            check_interval,
            float(os.getenv("FILE_TASK_STATUS_LOG_INTERVAL_SECONDS", "10")),
        )
        status_ttl_seconds = int(os.getenv("FILE_TASK_STATUS_TTL_SECONDS", "21600"))
        started_at = time.monotonic()
        last_progress_at = started_at
        last_done_count = -1
        last_status_log_at = 0.0

        if total_files == 0:
            await self.task_manager.update_task(
                task_id,
                progress=90.0,
                processed_files=0,
            )
            return

        while True:
            # 从 Redis 获取文件任务状态
            completed_count = 0
            failed_count = 0
            translating_count = 0
            pending_count = 0
            unknown_count = 0

            for file_id, file_task in file_tasks.items():
                # 从缓存读取状态
                status_key = f"file_task:{task_id}:{file_id}:status"
                status = await cache_service.redis.get(status_key)

                if status:
                    # Redis 可能返回字符串或字节，统一处理
                    if isinstance(status, bytes):
                        status = status.decode("utf-8")

                    if status == FileTaskStatus.COMPLETED.value:
                        completed_count += 1
                        file_task.status = FileTaskStatus.COMPLETED
                    elif status == FileTaskStatus.FAILED.value:
                        failed_count += 1
                        file_task.status = FileTaskStatus.FAILED
                    elif status == FileTaskStatus.TRANSLATING.value:
                        translating_count += 1
                        file_task.status = FileTaskStatus.TRANSLATING
                    elif status == FileTaskStatus.PENDING.value:
                        pending_count += 1
                        file_task.status = FileTaskStatus.PENDING
                    else:
                        unknown_count += 1
                else:
                    # Redis 中无状态记录：通常是消息尚未消费或 worker 异常退出
                    unknown_count += 1

            done_count = completed_count + failed_count
            now = time.monotonic()
            if done_count > last_done_count:
                last_done_count = done_count
                last_progress_at = now

            # 更新主任务进度
            progress = 20.0 + (70.0 * completed_count / total_files)
            await self.task_manager.update_task(
                task_id,
                progress=progress,
                processed_files=completed_count,
            )

            if now - last_status_log_at >= status_log_interval:
                elapsed = int(now - started_at)
                stalled_for = int(now - last_progress_at)
                print(
                    f"[{task_id}] 文件翻译进度: {completed_count}/{total_files} "
                    f"(失败: {failed_count}, 翻译中: {translating_count}, "
                    f"待处理: {pending_count}, 未知: {unknown_count}, "
                    f"耗时: {elapsed}s, 停滞: {stalled_for}s)"
                )
                last_status_log_at = now

            # 检查是否全部完成
            if done_count >= total_files:
                if failed_count > 0:
                    error_msg = f"有 {failed_count} 个文件翻译失败"
                    print(f"[{task_id}] {error_msg}")
                    await self.task_manager.update_task(
                        task_id, status=TaskStatus.FAILED, error_message=error_msg
                    )
                    raise Exception(error_msg)
                break

            elapsed = now - started_at
            stalled_for = now - last_progress_at
            if elapsed >= timeout_seconds or stalled_for >= stall_timeout_seconds:
                unresolved_ids = [
                    file_id
                    for file_id, file_task in file_tasks.items()
                    if file_task.status
                    not in {FileTaskStatus.COMPLETED, FileTaskStatus.FAILED}
                ]
                unresolved_paths = [
                    file_tasks[file_id].file_path for file_id in unresolved_ids[:10]
                ]
                unresolved_hint = ", ".join(unresolved_paths) if unresolved_paths else "-"
                timeout_reason = (
                    f"文件翻译超时/停滞 (总耗时 {int(elapsed)}s, "
                    f"停滞 {int(stalled_for)}s, 未完成 {len(unresolved_ids)} 个)"
                )
                print(
                    f"[{task_id}] {timeout_reason}；示例未完成文件: {unresolved_hint}"
                )

                for file_id in unresolved_ids:
                    status_key = f"file_task:{task_id}:{file_id}:status"
                    error_key = f"file_task:{task_id}:{file_id}:error"
                    await cache_service.redis.set(
                        status_key, FileTaskStatus.FAILED.value, ex=status_ttl_seconds
                    )
                    await cache_service.redis.set(
                        error_key, timeout_reason, ex=status_ttl_seconds
                    )
                    file_tasks[file_id].status = FileTaskStatus.FAILED
                    file_tasks[file_id].error_message = timeout_reason

                await self.task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    error_message=timeout_reason,
                )
                raise TimeoutError(timeout_reason)

            await asyncio.sleep(check_interval)


if __name__ == "__main__":
    worker = CoordinatorWorker()
    worker.start()
