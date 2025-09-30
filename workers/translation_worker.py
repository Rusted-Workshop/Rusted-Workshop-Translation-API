"""
翻译任务处理 Worker
"""

import json
import os
import shutil
import tempfile
import traceback

from pika.spec import Basic, BasicProperties

from core.rwmod import RWMod
from core.translate import translate_inifile
from models.task import TaskStatus
from services.cache_service import TranslationCache
from services.rabbitmq_service import get_rabbitmq_service
from services.s3_service import S3Service, create_archive, extract_archive
from services.task_manager import TaskManager
from utlis.ini_lib import IniFile


class TranslationWorker:
    """翻译任务处理 Worker"""

    QUEUE_NAME = "translation_tasks"

    def __init__(self):
        self.rabbitmq = get_rabbitmq_service()
        self.task_manager = TaskManager()
        self.s3_service = S3Service()
        self.cache_service = TranslationCache()
        self._event_loop = None

    def start(self):
        """启动 Worker"""
        print("Translation Worker 启动中...")
        self.rabbitmq.connect()
        self.rabbitmq.declare_queue(self.QUEUE_NAME)
        print(f"开始监听队列: {self.QUEUE_NAME}")
        self.rabbitmq.consume_messages(
            self.QUEUE_NAME, self.process_message, prefetch_count=1
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

        message: dict = {}
        task_id = None

        try:
            message = json.loads(body)
            task_id = message["task_id"]

            print(f"[{task_id}] 开始处理翻译任务")

            # 执行翻译任务
            self._process_translation_task(message)

            # 确认消息
            self.rabbitmq.ack_message(method.delivery_tag)
            print(f"[{task_id}] 任务处理完成")

        except Exception as e:
            print(f"处理消息失败: {e}")
            traceback.print_exc()
            # 拒绝消息，不重新入队
            self.rabbitmq.nack_message(method.delivery_tag, requeue=False)

            # 更新任务状态为失败
            if task_id:
                try:
                    # 创建新的事件循环来更新任务状态
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(
                            self.task_manager.update_task(
                                task_id,
                                status=TaskStatus.FAILED,
                                error_message=str(e),
                            )
                        )
                    finally:
                        loop.close()
                except Exception as update_error:
                    print(f"更新任务状态失败: {update_error}")
                    traceback.print_exc()

    def _process_translation_task(self, message: dict):
        """
        处理翻译任务

        Args:
            message: 任务消息
        """
        import asyncio

        asyncio.run(self._process_translation_task_async(message))

    async def _process_translation_task_async(self, message: dict):
        """
        处理翻译任务

        Args:
            message: 任务消息
        """
        task_id = message["task_id"]
        s3_source_url = message["s3_source_url"]
        s3_dest_bucket = message["s3_dest_bucket"]
        s3_dest_key = message["s3_dest_key"]
        target_language = message.get("target_language", "中文")

        # 创建临时工作目录
        work_dir = tempfile.mkdtemp(prefix=f"translation_{task_id}_")

        try:
            # 1. 下载文件
            print(f"[{task_id}] 下载文件: {s3_source_url}")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.DOWNLOADING, progress=10.0
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
                task_id, status=TaskStatus.EXTRACTING, progress=20.0
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

            # 3. 分析和翻译
            print(f"[{task_id}] 分析模组")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.ANALYZING, progress=30.0
            )

            try:
                rwmod = RWMod(extract_dir)

                # 分析风格
                print(f"[{task_id}] 分析风格")
                style = await rwmod.analysis_style()
                rwmod.style = style
            except Exception as e:
                error_msg = f"分析模组失败: {str(e)}"
                print(f"[{task_id}] {error_msg}")
                await self.task_manager.update_task(
                    task_id, status=TaskStatus.FAILED, error_message=error_msg
                )
                raise

            # 4. 翻译所有文件
            print(f"[{task_id}] 开始翻译")
            await self.task_manager.update_task(
                task_id,
                status=TaskStatus.TRANSLATING,
                progress=40.0,
                total_files=len(rwmod.unit_datas),
            )

            try:
                # 顺序翻译所有文件（旧版 worker，建议使用新的 coordinator + file workers）
                total_files = len(rwmod.unit_datas)

                print(f"[{task_id}] 开始顺序翻译 {total_files} 个文件")

                for idx, inifile in enumerate(rwmod.unit_datas):
                    file_name = os.path.basename(inifile.path)
                    print(
                        f"[{task_id}] 翻译文件: {file_name} ({idx + 1}/{total_files})"
                    )

                    # 翻译文件（使用新的简化版本）
                    translated_inifile = await translate_inifile(
                        inifile, translate_style=style, target_language=target_language
                    )

                    # 保存文件
                    self._save_inifile(translated_inifile)

                    # 更新进度
                    progress = 40.0 + (50.0 * (idx + 1) / total_files)
                    await self.task_manager.update_task(
                        task_id,
                        progress=progress,
                        current_file=file_name,
                        processed_files=idx + 1,
                    )

                print(f"[{task_id}] 翻译完成，共处理 {total_files} 个文件")

            except Exception as e:
                import traceback

                error_msg = f"翻译文件失败: {str(e)}"
                error_details = traceback.format_exc()
                print(f"[{task_id}] {error_msg}")
                print(f"[{task_id}] 错误详情:\n{error_details}")

                await self.task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    error_message=f"{error_msg}\n\n详细信息:\n{error_details[:500]}",
                )
                raise

            # 5. 打包
            print(f"[{task_id}] 打包文件")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.MERGING, progress=90.0
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

            # 6. 上传到S3
            print(f"[{task_id}] 上传到 S3")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.UPLOADING, progress=95.0
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

            # 7. 完成
            print(f"[{task_id}] 任务完成: {s3_url}")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.COMPLETED, progress=100.0
            )

        except Exception:
            # 所有异常都已在上面处理并更新了任务状态，这里只需重新抛出
            raise
        finally:
            # 清理临时文件
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
                print(f"[{task_id}] 清理临时文件")

    def _inifile_to_string(self, inifile: IniFile) -> str:
        """将INI文件对象转换为字符串"""
        lines = []
        for section, data in inifile.data.items():
            lines.append(f"[{section}]")
            for key, value in data.items():
                lines.append(f"{key}={value}")
        return "\n".join(lines)

    def _save_inifile(self, inifile: IniFile):
        """保存INI文件"""
        import configparser

        config = configparser.RawConfigParser()

        for section, data in inifile.data.items():
            config.add_section(section)
            for key, value in data.items():
                # 将换行符转换回 \n
                value = value.replace("\n", "\\n")
                config.set(section, key, value)

        with open(inifile.path, "w", encoding="utf-8") as f:
            config.write(f, space_around_delimiters=False)


if __name__ == "__main__":
    worker = TranslationWorker()
    worker.start()
