"""
FastAPI 主应用
"""

import uuid
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from models.task import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskResponse,
    TaskRunRequest,
    TaskStatus,
    TranslationTask,
)
from services.rabbitmq_service import get_rabbitmq_service
from services.s3_service import S3Service
from services.task_manager import TaskManager
from utlis.config import (
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
    description="模组翻译服务 API",
    version="1.0.0",
)

# 添加 CORS 中间件
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


@app.get("/")
def read_root():
    """健康检查"""
    return {"status": "ok", "service": "Translation API"}


@app.post("/tasks", response_model=TaskCreateResponse, status_code=201)
async def create_task(request: TaskCreateRequest):
    """
    创建翻译任务

    生成任务ID和预签名上传URL，用户需要先上传文件到返回的URL，然后调用 /tasks/run 启动任务。

    工作流程：
    1. 调用此接口创建任务，获取 task_id 和 upload_url
    2. 使用 HTTP PUT 方法上传文件到 upload_url
    3. 调用 POST /tasks/run 启动翻译任务
    """
    # 生成任务ID
    task_id = str(uuid.uuid4())

    # 生成S3路径
    s3_source_key = f"{S3_UPLOAD_PREFIX}/{task_id}/source.rwmod"
    s3_dest_key = f"{S3_OUTPUT_PREFIX}/{task_id}/translated.rwmod"

    # 生成预签名上传URL
    try:
        upload_url = s3_service.generate_presigned_upload_url(
            bucket=S3_BUCKET,
            key=s3_source_key,
            expiration=3600,
            content_type="application/zip",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate upload URL: {str(e)}"
        )

    # 创建任务（但不启动）
    s3_source_url = f"s3://{S3_BUCKET}/{s3_source_key}"
    task = TranslationTask(
        task_id=task_id,
        s3_source_url=s3_source_url,
        s3_dest_bucket=S3_BUCKET,
        s3_dest_key=s3_dest_key,
        target_language=request.target_language,
        status=TaskStatus.PENDING,
    )

    # 保存任务状态
    await task_manager.create_task(task)

    return TaskCreateResponse(
        task_id=task_id,
        upload_url=upload_url,
        expires_in=3600,
        target_language=request.target_language,
    )


@app.post("/tasks/run", response_model=TaskResponse)
async def run_task(request: TaskRunRequest):
    """
    运行翻译任务

    在用户上传文件后，调用此接口启动翻译任务。
    如果任务状态为 FAILED，会重置任务状态并重新运行。
    """
    # 获取任务
    task = await task_manager.get_task(request.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 检查任务状态
    if task.status == TaskStatus.FAILED:
        # 失败的任务可以重试，重置状态
        await task_manager.update_task(
            request.task_id,
            status=TaskStatus.PENDING,
            progress=0.0,
            error_message=None,
            processed_files=0,
        )
        task = await task_manager.get_task(request.task_id)
    elif task.status != TaskStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Task is already {task.status.value}, cannot run again",
        )

    # 发送消息到 RabbitMQ
    try:
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

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
            },
        )
        rabbitmq.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {str(e)}")

    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        progress=round(task.progress, 2),
        total_files=task.total_files,
        processed_files=task.processed_files,
        error_message=task.error_message,
        download_url=None,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """
    查询任务状态

    通过任务ID获取任务的详细信息和进度
    """
    task = await task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 如果任务已完成，生成下载链接
    download_url = None
    if task.status == TaskStatus.COMPLETED:
        try:
            download_url = s3_service.generate_presigned_download_url(
                bucket=task.s3_dest_bucket,
                key=task.s3_dest_key,
                expiration=3600,
            )
        except Exception as e:
            print(f"Failed to generate download URL: {e}")

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


@app.get("/tasks", response_model=List[TaskResponse])
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """
    列出所有任务

    按创建时间倒序返回任务列表
    """
    tasks = await task_manager.list_tasks(limit=limit, offset=offset)

    result = []
    for task in tasks:
        # 如果任务已完成，生成下载链接
        download_url = None
        if task.status == TaskStatus.COMPLETED:
            try:
                download_url = s3_service.generate_presigned_download_url(
                    bucket=task.s3_dest_bucket,
                    key=task.s3_dest_key,
                    expiration=3600,
                )
            except Exception as e:
                print(f"Failed to generate download URL for task {task.task_id}: {e}")

        result.append(
            TaskResponse(
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
        )

    return result


@app.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str):
    """
    删除任务

    删除指定的任务记录
    """
    result = await task_manager.delete_task(task_id)

    if not result:
        raise HTTPException(status_code=404, detail="Task not found")

    return None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
