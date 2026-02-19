"""
文件翻译 Worker

专门负责翻译单个文件，接收协调器发送的文件级任务
"""

import json
import os
import traceback

from pika.spec import Basic, BasicProperties

from core.translate import translate_file_preserve_structure
from models.file_task import FileTaskStatus, FileTranslationMessage
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

        self.rabbitmq.consume_messages(
            self.QUEUE_NAME, self.process_message, prefetch_count=prefetch_count
        )

    def process_message(
        self,
        channel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ):
        """处理消息"""
        import asyncio

        message_data = {}
        task_id = None
        file_id = None

        try:
            message_data = json.loads(body)
            message = FileTranslationMessage(**message_data)

            task_id = message.task_id
            file_id = message.file_id
            file_path = message.file_path

            print(f"[{task_id}:{file_id}] 开始翻译文件: {file_path}")

            # 使用 asyncio.run() 创建独立的 event loop 并正确清理
            asyncio.run(self._process_file_async(message))

            # 确认消息
            self.rabbitmq.ack_message(method.delivery_tag)
            print(f"[{task_id}:{file_id}] 文件翻译完成: {file_path}")

        except Exception as e:
            print(f"[{task_id}:{file_id}] 翻译文件失败: {e}")
            traceback.print_exc()

            # 拒绝消息，不重新入队
            # 注意：失败状态已在 _process_file_async 的 except 块中更新
            self.rabbitmq.nack_message(method.delivery_tag, requeue=False)

    async def _process_file_async(self, message: FileTranslationMessage):
        """
        异步处理文件翻译（完整流程）

        Args:
            message: 文件翻译消息
        """
        task_id = message.task_id
        file_id = message.file_id

        # 在当前 event loop 中创建新的 Redis 连接
        cache_service = TranslationCache()

        try:
            # 更新状态为翻译中
            await self._update_file_task_status(
                task_id, file_id, FileTaskStatus.TRANSLATING, cache_service
            )

            # 执行翻译
            await self._translate_file(message)

            # 更新状态为完成
            await self._update_file_task_status(
                task_id, file_id, FileTaskStatus.COMPLETED, cache_service
            )

        except Exception as e:
            # 更新状态为失败
            await self._update_file_task_status(
                task_id, file_id, FileTaskStatus.FAILED, cache_service, str(e)
            )
            raise
        finally:
            # 关闭 Redis 连接
            await cache_service.redis.aclose()

    async def _translate_file(self, message: FileTranslationMessage):
        """
        翻译单个文件

        Args:
            message: 文件翻译消息
        """
        task_id = message.task_id
        file_id = message.file_id
        file_path = message.file_path
        work_dir = message.work_dir
        translate_style = message.translate_style
        target_language = message.target_language

        # 构建完整文件路径（将正斜杠转换为系统路径分隔符）
        file_path_normalized = file_path.replace("/", os.sep)
        full_path = os.path.join(work_dir, file_path_normalized)

        print(f"[{task_id}:{file_id}] 完整路径: {full_path}")

        # 检查文件是否存在
        if not os.path.exists(full_path):
            raise FileNotFoundError(
                f"文件不存在: {full_path}\n工作目录: {work_dir}\n相对路径: {file_path}"
            )

        # 保留原文件结构，仅替换可翻译文本字段
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
        """
        更新文件任务状态到 Redis

        Args:
            task_id: 主任务ID
            file_id: 文件ID
            status: 状态
            cache_service: Redis 缓存服务实例
            error_message: 错误信息
        """
        status_key = f"file_task:{task_id}:{file_id}:status"
        await cache_service.redis.set(status_key, status.value, ex=3600)

        if error_message:
            error_key = f"file_task:{task_id}:{file_id}:error"
            await cache_service.redis.set(error_key, error_message, ex=3600)

if __name__ == "__main__":
    worker = FileTranslationWorker()
    worker.start()

