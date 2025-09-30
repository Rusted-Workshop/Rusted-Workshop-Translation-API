"""
启动翻译 Worker 进程
"""

from workers.translation_worker import TranslationWorker

if __name__ == "__main__":
    worker = TranslationWorker()
    worker.start()
