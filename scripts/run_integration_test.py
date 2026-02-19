"""
端到端集成测试（不依赖 HTTP 服务）。

流程：
1. 上传 rwmod 到 S3
2. 创建任务（Postgres）
3. 投递 RabbitMQ 主任务消息
4. 启动 coordinator + file worker
5. 轮询任务直到完成/失败
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.task import TaskStatus, TranslationTask
from services.rabbitmq_service import get_rabbitmq_service
from services.s3_service import S3Service
from services.task_manager import TaskManager
from utils.config import (
    AWS_ACCESS_KEY_ID,
    AWS_ENDPOINT_URL,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY,
    S3_BUCKET,
    S3_OUTPUT_PREFIX,
    S3_UPLOAD_PREFIX,
)


@dataclass
class WorkerProc:
    name: str
    process: subprocess.Popen
    log_file: object


def start_worker(module: str, log_dir: Path, name: str | None = None) -> WorkerProc:
    worker_name = name or module.replace(".", "_")
    log_path = log_dir / f"{worker_name}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=log_file,
        stderr=log_file,
        env=env,
    )
    return WorkerProc(name=worker_name, process=proc, log_file=log_file)


def stop_worker(worker: WorkerProc) -> None:
    try:
        if worker.process.poll() is None:
            worker.process.terminate()
            try:
                worker.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.process.kill()
                worker.process.wait(timeout=5)
    finally:
        worker.log_file.close()


def purge_integration_queues() -> None:
    rabbitmq = get_rabbitmq_service()
    rabbitmq.connect()
    try:
        rabbitmq.declare_queue("translation_tasks")
        rabbitmq.declare_queue("file_translation_tasks")
        rabbitmq.purge_queue("translation_tasks")
        rabbitmq.purge_queue("file_translation_tasks")
        print("Purged queues: translation_tasks, file_translation_tasks")
    finally:
        rabbitmq.close()


async def submit_task(
    file_path: Path,
    target_language: str,
    translate_style: str,
) -> str:
    task_manager = TaskManager()
    s3_service = S3Service(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
        endpoint_url=AWS_ENDPOINT_URL,
    )

    task_id = str(uuid.uuid4())
    s3_source_key = f"{S3_UPLOAD_PREFIX}/{task_id}/source.rwmod"
    s3_dest_key = f"{S3_OUTPUT_PREFIX}/{task_id}/translated.rwmod"

    await s3_service.upload_file(str(file_path), S3_BUCKET, s3_source_key)

    task = TranslationTask(
        task_id=task_id,
        s3_source_url=f"s3://{S3_BUCKET}/{s3_source_key}",
        s3_dest_bucket=S3_BUCKET,
        s3_dest_key=s3_dest_key,
        target_language=target_language,
        translate_style=translate_style,
        status=TaskStatus.PENDING,
    )
    await task_manager.create_task(task)

    rabbitmq = get_rabbitmq_service()
    rabbitmq.connect()
    rabbitmq.declare_queue("translation_tasks")
    rabbitmq.publish_message(
        "translation_tasks",
        {
            "task_id": task.task_id,
            "s3_source_url": task.s3_source_url,
            "s3_dest_bucket": task.s3_dest_bucket,
            "s3_dest_key": task.s3_dest_key,
            "target_language": task.target_language,
            "translate_style": task.translate_style,
        },
    )
    rabbitmq.close()

    return task_id


async def poll_task(
    task_id: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> TranslationTask:
    task_manager = TaskManager()

    max_polls = max(1, timeout_seconds // poll_interval_seconds)
    last_task: TranslationTask | None = None

    for idx in range(max_polls):
        task = await task_manager.get_task(task_id)
        if task:
            last_task = task
            print(
                f"POLL {idx + 1}: status={task.status.value} "
                f"progress={task.progress:.2f} "
                f"processed={task.processed_files}/{task.total_files}"
            )
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                return task
        else:
            print(f"POLL {idx + 1}: task not found yet")

        await asyncio.sleep(poll_interval_seconds)

    raise TimeoutError(
        f"Task {task_id} did not finish within {timeout_seconds}s. "
        f"Last status: {last_task.status.value if last_task else 'unknown'}"
    )


async def run(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    purge_integration_queues()

    workers: list[WorkerProc] = []
    try:
        workers.append(
            start_worker(
                "workers.coordinator_worker",
                log_dir,
                name="workers_coordinator_worker",
            )
        )
        for idx in range(args.file_worker_count):
            workers.append(
                start_worker(
                    "workers.file_translation_worker",
                    log_dir,
                    name=f"workers_file_translation_worker_{idx + 1}",
                )
            )
        print(f"Started workers: coordinator=1 file_workers={args.file_worker_count}")

        # 等待 worker 建立 MQ 消费连接
        await asyncio.sleep(3)

        task_id = await submit_task(
            file_path=Path(args.file).resolve(),
            target_language=args.target_language,
            translate_style=args.translate_style,
        )
        print(f"TASK_ID={task_id}")

        final_task = await poll_task(
            task_id=task_id,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )

        print(f"FINAL_STATUS={final_task.status.value}")
        print(f"FINAL_PROGRESS={final_task.progress:.2f}")
        print(f"FINAL_ERROR={final_task.error_message}")

        if final_task.status == TaskStatus.COMPLETED:
            s3_service = S3Service(
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
                endpoint_url=AWS_ENDPOINT_URL,
            )
            download_url = s3_service.generate_presigned_download_url(
                bucket=final_task.s3_dest_bucket,
                key=final_task.s3_dest_key,
                expiration=3600,
            )
            print(f"RESULT_URL={download_url}")
            return 0

        return 2
    finally:
        for worker in reversed(workers):
            stop_worker(worker)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        default="tmp7cgm8rh1.rwmod",
        help="待测试 rwmod 文件路径",
    )
    parser.add_argument(
        "--target-language",
        default="zh-CN",
        help="目标语言",
    )
    parser.add_argument(
        "--translate-style",
        default="auto",
        help="翻译风格",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=1800,
        help="轮询超时时间（秒）",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=5,
        help="轮询间隔（秒）",
    )
    parser.add_argument(
        "--file-worker-count",
        type=int,
        default=15,
        help="文件翻译 worker 进程数",
    )
    return parser.parse_args()


if __name__ == "__main__":
    ns = parse_args()
    if not os.path.exists(ns.file):
        print(f"File not found: {ns.file}")
        raise SystemExit(1)
    raise SystemExit(asyncio.run(run(ns)))
