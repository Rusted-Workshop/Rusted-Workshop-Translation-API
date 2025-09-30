"""
启动清理 Worker 进程
"""

import os

from workers.cleanup_worker import CleanupWorker

if __name__ == "__main__":
    work_dir = os.getenv("WORK_DIR", "/tmp/translation_work")
    retention_days = int(os.getenv("RETENTION_DAYS", "7"))
    check_interval = int(os.getenv("CHECK_INTERVAL", "3600"))

    worker = CleanupWorker(
        work_dir=work_dir,
        retention_days=retention_days,
        check_interval=check_interval,
    )
    worker.start()
