"""
FastAPI 主应用（V1）
"""

import os
import tempfile
import uuid
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from models.task import TaskResponse, TaskStatus, TranslationTask
from services.rabbitmq_service import get_rabbitmq_service
from services.s3_service import S3Service
from services.task_manager import InvalidTaskStateTransition, TaskManager
from utils.config import (
    AWS_ACCESS_KEY_ID,
    AWS_ENDPOINT_URL,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY,
    S3_BUCKET,
    S3_OUTPUT_PREFIX,
    S3_UPLOAD_PREFIX,
)

app = FastAPI(
    title="Rusted Workshop Translation API",
    description="模组翻译服务 API（v1）",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

task_manager = TaskManager()
s3_service = S3Service(
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
    endpoint_url=AWS_ENDPOINT_URL,
)


def _raise_api_error(
    status_code: int,
    code: str,
    message: str,
    details: Optional[dict] = None,
) -> None:
    payload: dict = {
        "code": code,
        "message": message,
    }
    if details:
        payload["details"] = details
    raise HTTPException(status_code=status_code, detail=payload)


def _to_task_response(
    task: TranslationTask, download_url: str | None = None
) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        progress=round(task.progress, 2),
        total_files=task.total_files,
        processed_files=task.processed_files,
        error_message=task.error_message,
        download_url=download_url,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def _queue_task(task: TranslationTask) -> None:
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


@app.get("/health")
def health():
    return {"status": "ok", "service": "translation-api", "version": "v1"}


@app.post("/v1/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    file: UploadFile = File(...),
    target_language: str = Form(default="zh-CN"),
    translate_style: str = Form(default="auto"),
):
    if not file.filename:
        _raise_api_error(400, "INVALID_FILE", "Missing upload file name")
    if not str(file.filename).lower().endswith(".rwmod"):
        _raise_api_error(400, "INVALID_FILE_TYPE", "Only .rwmod files are allowed")

    task_id = str(uuid.uuid4())
    s3_source_key = f"{S3_UPLOAD_PREFIX}/{task_id}/source.rwmod"
    s3_dest_key = f"{S3_OUTPUT_PREFIX}/{task_id}/translated.rwmod"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".rwmod") as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name

    try:
        await s3_service.upload_file(tmp_path, S3_BUCKET, s3_source_key)
    except Exception as e:
        _raise_api_error(
            500, "S3_UPLOAD_FAILED", "Failed to upload source file", {"error": str(e)}
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    task = TranslationTask(
        task_id=task_id,
        s3_source_url=f"s3://{S3_BUCKET}/{s3_source_key}",
        s3_dest_bucket=S3_BUCKET,
        s3_dest_key=s3_dest_key,
        target_language=target_language,
        translate_style=translate_style,
        status=TaskStatus.PENDING,
    )

    try:
        task = await task_manager.create_task(task)
    except Exception as e:
        _raise_api_error(
            500, "TASK_CREATE_FAILED", "Failed to create task", {"error": str(e)}
        )

    try:
        _queue_task(task)
    except Exception as e:
        await task_manager.update_task(
            task.task_id,
            status=TaskStatus.FAILED,
            error_message=f"Failed to queue task: {str(e)}",
        )
        _raise_api_error(
            500, "QUEUE_PUBLISH_FAILED", "Failed to queue task", {"error": str(e)}
        )

    return _to_task_response(task)


@app.get("/v1/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    task = await task_manager.get_task(task_id)
    if not task:
        _raise_api_error(404, "TASK_NOT_FOUND", "Task not found")

    download_url = None
    if task.status == TaskStatus.COMPLETED:
        try:
            download_url = s3_service.generate_presigned_download_url(
                bucket=task.s3_dest_bucket,
                key=task.s3_dest_key,
                expiration=3600,
            )
        except Exception:
            download_url = None

    return _to_task_response(task, download_url)


# @app.get("/v1/tasks", response_model=List[TaskResponse])
# async def list_tasks(
#     limit: int = Query(default=50, ge=1, le=100),
#     offset: int = Query(default=0, ge=0),
# ):
#     tasks = await task_manager.list_tasks(limit=limit, offset=offset)
#     return [_to_task_response(task) for task in tasks]


@app.delete("/v1/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str):
    result = await task_manager.delete_task(task_id)
    if not result:
        _raise_api_error(404, "TASK_NOT_FOUND", "Task not found")
    return None


@app.post("/v1/tasks/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str):
    task = await task_manager.get_task(task_id)
    if not task:
        _raise_api_error(404, "TASK_NOT_FOUND", "Task not found")

    if task.status != TaskStatus.FAILED:
        _raise_api_error(400, "TASK_NOT_RETRYABLE", "Only failed tasks can be retried")

    try:
        task = await task_manager.update_task(
            task_id,
            status=TaskStatus.PENDING,
            progress=0.0,
            processed_files=0,
            error_message=None,
        )
    except InvalidTaskStateTransition as e:
        _raise_api_error(409, "INVALID_TASK_STATE", str(e))

    if not task:
        _raise_api_error(404, "TASK_NOT_FOUND", "Task not found")

    try:
        _queue_task(task)
    except Exception as e:
        await task_manager.update_task(
            task.task_id,
            status=TaskStatus.FAILED,
            error_message=f"Failed to queue retry: {str(e)}",
        )
        _raise_api_error(
            500, "QUEUE_PUBLISH_FAILED", "Failed to queue retry", {"error": str(e)}
        )

    return _to_task_response(task)


@app.get("/v1/tasks/{task_id}/result-url")
async def get_result_url(task_id: str):
    task = await task_manager.get_task(task_id)
    if not task:
        _raise_api_error(404, "TASK_NOT_FOUND", "Task not found")
    if task.status != TaskStatus.COMPLETED:
        _raise_api_error(400, "TASK_NOT_COMPLETED", "Task is not completed yet")

    try:
        download_url = s3_service.generate_presigned_download_url(
            bucket=task.s3_dest_bucket,
            key=task.s3_dest_key,
            expiration=3600,
        )
    except Exception as e:
        _raise_api_error(
            500, "PRESIGN_FAILED", "Failed to generate download URL", {"error": str(e)}
        )

    return {"task_id": task_id, "download_url": download_url, "expires_in": 3600}
