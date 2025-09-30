"""
FastAPI 主应用
"""

import uuid
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from models.task import TaskCreateRequest, TaskResponse, TaskStatus, TranslationTask
from services.rabbitmq_service import get_rabbitmq_service
from services.task_manager import TaskManager

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


@app.get("/")
def read_root():
    """健康检查"""
    return {"status": "ok", "service": "Translation API"}


@app.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(request: TaskCreateRequest):
    """
    创建翻译任务

    提交一个新的翻译任务到队列
    """
    # 生成任务ID
    task_id = str(uuid.uuid4())

    # 创建任务
    task = TranslationTask(
        task_id=task_id,
        s3_source_url=request.s3_source_url,
        s3_dest_bucket=request.s3_dest_bucket,
        s3_dest_key=request.s3_dest_key,
        target_language=request.target_language,
        status=TaskStatus.PENDING,
    )

    # 保存任务状态
    await task_manager.create_task(task)

    # 发送消息到 RabbitMQ
    try:
        rabbitmq = get_rabbitmq_service()
        rabbitmq.connect()
        rabbitmq.declare_queue("translation_tasks")
        rabbitmq.publish_message(
            "translation_tasks",
            {
                "task_id": task_id,
                "s3_source_url": request.s3_source_url,
                "s3_dest_bucket": request.s3_dest_bucket,
                "s3_dest_key": request.s3_dest_key,
                "target_language": request.target_language,
            },
        )
        rabbitmq.close()
    except Exception as e:
        # 如果发送失败，删除任务
        await task_manager.delete_task(task_id)
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {str(e)}")

    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_file=task.current_file,
        total_files=task.total_files,
        processed_files=task.processed_files,
        error_message=task.error_message,
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

    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_file=task.current_file,
        total_files=task.total_files,
        processed_files=task.processed_files,
        error_message=task.error_message,
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

    return [
        TaskResponse(
            task_id=task.task_id,
            status=task.status,
            progress=task.progress,
            current_file=task.current_file,
            total_files=task.total_files,
            processed_files=task.processed_files,
            error_message=task.error_message,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
        )
        for task in tasks
    ]


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
