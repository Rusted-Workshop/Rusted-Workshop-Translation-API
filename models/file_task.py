"""
文件翻译子任务模型定义
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FileTaskStatus(str, Enum):
    """文件翻译任务状态枚举"""

    PENDING = "pending"  # 待处理
    TRANSLATING = "translating"  # 翻译中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败


class FileTranslationTask(BaseModel):
    """文件翻译子任务模型"""

    task_id: str = Field(..., description="主任务ID")
    file_id: str = Field(..., description="文件唯一ID")
    file_path: str = Field(..., description="文件相对路径")
    status: FileTaskStatus = Field(
        default=FileTaskStatus.PENDING, description="任务状态"
    )
    retry_count: int = Field(default=0, description="重试次数")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")


class FileTranslationMessage(BaseModel):
    """文件翻译消息"""

    task_id: str = Field(..., description="主任务ID")
    file_id: str = Field(..., description="文件唯一ID")
    file_path: str = Field(..., description="文件相对路径")
    work_dir: str = Field(..., description="工作目录")
    translate_style: str = Field(..., description="翻译风格")
    target_language: str = Field(default="中文", description="目标语言")
