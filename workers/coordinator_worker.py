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
import json
import os
import shutil
import tempfile
import traceback
import uuid
from typing import Dict

from pika.spec import Basic, BasicProperties

from core.rwmod import RWMod
from models.file_task import FileTaskStatus, FileTranslationMessage, FileTranslationTask
from models.task import TaskStatus
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
        print(f"开始监听队列: {self.MAIN_QUEUE_NAME}")
        self.rabbitmq.consume_messages(
            self.MAIN_QUEUE_NAME, self.process_message, prefetch_count=1
        )

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

        if current_task.status != TaskStatus.PENDING:
            print(
                f"[{task_id}] 任务当前状态为 {current_task.status.value}，"
                "跳过重复/并发消息"
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

                # 发送到文件翻译队列
                file_message = FileTranslationMessage(
                    task_id=task_id,
                    file_id=file_id,
                    file_path=file_path,
                    work_dir=extract_dir,
                    translate_style=style,
                    target_language=target_language,
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
        check_interval = 2  # 每2秒检查一次

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

            # 更新主任务进度
            progress = 20.0 + (70.0 * completed_count / total_files)
            await self.task_manager.update_task(
                task_id,
                progress=progress,
                processed_files=completed_count,
            )

            print(
                f"[{task_id}] 文件翻译进度: {completed_count}/{total_files} "
                f"(失败: {failed_count})"
            )

            # 检查是否全部完成
            if completed_count + failed_count >= total_files:
                if failed_count > 0:
                    error_msg = f"有 {failed_count} 个文件翻译失败"
                    print(f"[{task_id}] {error_msg}")
                    await self.task_manager.update_task(
                        task_id, status=TaskStatus.FAILED, error_message=error_msg
                    )
                    raise Exception(error_msg)
                break

            await asyncio.sleep(check_interval)


if __name__ == "__main__":
    worker = CoordinatorWorker()
    worker.start()
