"""
任务模型定义
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 待处理
    DOWNLOADING = "downloading"  # 下载中
    EXTRACTING = "extracting"  # 解压中
    ANALYZING = "analyzing"  # 分析中
    TRANSLATING = "translating"  # 翻译中
    MERGING = "merging"  # 合并中
    UPLOADING = "uploading"  # 上传中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败


class TranslationTask(BaseModel):
    """翻译任务模型"""

    task_id: str = Field(..., description="任务ID")
    s3_source_url: str = Field(..., description="S3源文件URL")
    s3_dest_bucket: str = Field(..., description="S3目标存储桶")
    s3_dest_key: str = Field(..., description="S3目标文件键")
    target_language: str = Field(default="中文", description="目标语言")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    progress: float = Field(default=0.0, description="进度百分比 (0-100)")
    current_file: Optional[str] = Field(default=None, description="当前处理的文件")
    total_files: int = Field(default=0, description="总文件数")
    processed_files: int = Field(default=0, description="已处理文件数")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")


class TaskCreateRequest(BaseModel):
    """创建任务请求"""

    target_language: str = Field(default="中文", description="目标语言")


class TaskCreateResponse(BaseModel):
    """创建任务响应"""

    task_id: str = Field(..., description="任务ID")
    upload_url: str = Field(..., description="预签名上传URL，用于上传源文件")
    expires_in: int = Field(..., description="上传URL过期时间（秒）")
    target_language: str = Field(..., description="目标语言")


class TaskRunRequest(BaseModel):
    """运行任务请求"""

    task_id: str = Field(..., description="任务ID")


class TaskResponse(BaseModel):
    """任务响应"""

    task_id: str
    status: TaskStatus
    progress: float
    current_file: Optional[str] = None
    total_files: int = 0
    processed_files: int = 0
    error_message: Optional[str] = None
    download_url: Optional[str] = Field(default=None, description="下载链接（任务完成后可用）")
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
