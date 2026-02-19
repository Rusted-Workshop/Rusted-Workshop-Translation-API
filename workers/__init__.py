"""Worker 进程模块（惰性导入，避免启动子模块时产生副作用）。"""

from importlib import import_module
from typing import Any

__all__ = ["CoordinatorWorker", "FileTranslationWorker", "CleanupWorker"]


def __getattr__(name: str) -> Any:
    if name == "CoordinatorWorker":
        return import_module("workers.coordinator_worker").CoordinatorWorker
    if name == "FileTranslationWorker":
        return import_module("workers.file_translation_worker").FileTranslationWorker
    if name == "CleanupWorker":
        return import_module("workers.cleanup_worker").CleanupWorker
    raise AttributeError(f"module 'workers' has no attribute {name!r}")
