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
        message: dict = {}
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
            if message and "task_id" in message:
                import asyncio

                asyncio.run(
                    self.task_manager.update_task(
                        message["task_id"],
                        status=TaskStatus.FAILED,
                        error_message=str(e),
                    )
                )

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
        处理翻译任务（异步）

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
            await self.s3_service.download_file(s3_source_url, archive_path)

            # 2. 解压文件
            print(f"[{task_id}] 解压文件")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.EXTRACTING, progress=20.0
            )

            extract_dir = os.path.join(work_dir, "extracted")
            extract_archive(archive_path, extract_dir)

            # 3. 分析和翻译
            print(f"[{task_id}] 分析模组")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.ANALYZING, progress=30.0
            )

            rwmod = RWMod(extract_dir)

            # 分析风格
            print(f"[{task_id}] 分析风格")
            style = await rwmod.analysis_style()
            rwmod.style = style

            # 4. 翻译所有文件
            print(f"[{task_id}] 开始翻译")
            await self.task_manager.update_task(
                task_id,
                status=TaskStatus.TRANSLATING,
                progress=40.0,
                total_files=len(rwmod.unit_datas),
            )

            for idx, inifile in enumerate(rwmod.unit_datas):
                file_name = os.path.basename(inifile.path)
                print(
                    f"[{task_id}] 翻译文件: {file_name} ({idx + 1}/{len(rwmod.unit_datas)})"
                )

                # 使用缓存
                translated_inifile = await self._translate_with_cache(
                    inifile, style, task_id, target_language
                )

                # 保存翻译结果
                self._save_inifile(translated_inifile)

                # 更新进度
                progress = 40.0 + (50.0 * (idx + 1) / len(rwmod.unit_datas))
                await self.task_manager.update_task(
                    task_id,
                    progress=progress,
                    current_file=file_name,
                    processed_files=idx + 1,
                )

            # 5. 打包
            print(f"[{task_id}] 打包文件")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.MERGING, progress=90.0
            )

            output_archive = os.path.join(work_dir, "translated.rwmod")
            create_archive(extract_dir, output_archive, format="zip")

            # 6. 上传到S3
            print(f"[{task_id}] 上传到 S3")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.UPLOADING, progress=95.0
            )

            s3_url = await self.s3_service.upload_file(
                output_archive, s3_dest_bucket, s3_dest_key
            )

            # 7. 完成
            print(f"[{task_id}] 任务完成: {s3_url}")
            await self.task_manager.update_task(
                task_id, status=TaskStatus.COMPLETED, progress=100.0
            )

        finally:
            # 清理临时文件
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
                print(f"[{task_id}] 清理临时文件")

    async def _translate_with_cache(
        self,
        inifile: IniFile,
        style: str,
        mod_id: str,
        target_language: str = "中文",
    ) -> IniFile:
        """
        使用缓存进行翻译

        Args:
            inifile: INI文件对象
            style: 翻译风格
            mod_id: 模组ID
            target_language: 目标语言

        Returns:
            翻译后的INI文件对象
        """
        # 尝试从缓存获取
        file_content = self._inifile_to_string(inifile)
        cached_translations = await self.cache_service.get_cached_translation(
            inifile.path, file_content
        )

        if cached_translations:
            print(f"  使用缓存翻译: {os.path.basename(inifile.path)}")
            # 应用缓存的翻译
            for section in inifile.data.keys():
                for key in inifile.data[section]:
                    original_text = inifile.data[section][key]
                    if original_text in cached_translations:
                        inifile.data[section][key] = cached_translations[original_text]
            return inifile

        # 执行翻译
        translated_inifile = await translate_inifile(
            inifile, translate_style=style, mod_id=mod_id
        )

        # 保存到缓存
        translations = {}
        for section in inifile.data.keys():
            for key in inifile.data[section]:
                original_text = inifile.data[section][key]
                translated_text = translated_inifile.data[section][key]
                if original_text != translated_text:
                    translations[original_text] = translated_text

        if translations:
            await self.cache_service.save_translation(
                inifile.path, file_content, translations
            )

        return translated_inifile

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
