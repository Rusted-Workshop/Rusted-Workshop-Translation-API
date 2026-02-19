"""
启动所有 Worker 进程
"""

import os
import signal
import subprocess
import sys
import time


def start_workers():
    """启动所有 worker 进程"""
    print("=" * 60)
    print("启动翻译服务 Workers")
    print("=" * 60)

    # 读取配置
    file_worker_count = int(os.getenv("FILE_WORKER_COUNT", "15"))

    processes = []

    try:
        # 1. 启动协调器 Worker（1个）
        print("\n[1/2] 启动协调器 Worker...")
        coordinator_process = subprocess.Popen(
            [sys.executable, "-m", "workers.coordinator_worker"],
            cwd=os.getcwd(),
        )
        processes.append(("Coordinator Worker", coordinator_process))
        print(f"✓ 协调器 Worker 已启动 (PID: {coordinator_process.pid})")
        time.sleep(1)

        # 2. 启动文件翻译 Workers（多个）
        print(f"\n[2/2] 启动 {file_worker_count} 个文件翻译 Workers...")
        for i in range(file_worker_count):
            worker_process = subprocess.Popen(
                [sys.executable, "-m", "workers.file_translation_worker"],
                cwd=os.getcwd(),
            )
            processes.append((f"File Worker #{i + 1}", worker_process))
            print(f"✓ 文件翻译 Worker #{i + 1} 已启动 (PID: {worker_process.pid})")
            time.sleep(0.5)

        print("\n" + "=" * 60)
        print("所有 Workers 已启动")
        print("按 Ctrl+C 停止所有 Workers")
        print("=" * 60 + "\n")

        # 监控进程
        while True:
            time.sleep(1)

            # 检查是否有进程意外退出
            for name, process in processes:
                if process.poll() is not None:
                    print(f"\n⚠ {name} 意外退出 (退出码: {process.returncode})")
                    return False

    except KeyboardInterrupt:
        print("\n\n收到停止信号，正在关闭所有 Workers...")

    finally:
        # 停止所有进程
        for name, process in processes:
            if process.poll() is None:
                print(f"停止 {name} (PID: {process.pid})...")
                try:
                    # Windows 使用 SIGTERM，Linux/Mac 可以用 SIGINT
                    if sys.platform == "win32":
                        process.terminate()
                    else:
                        process.send_signal(signal.SIGINT)

                    # 等待进程退出
                    try:
                        process.wait(timeout=5)
                        print(f"✓ {name} 已停止")
                    except subprocess.TimeoutExpired:
                        print(f"⚠ {name} 未响应，强制终止...")
                        process.kill()
                        process.wait()
                        print(f"✓ {name} 已强制终止")

                except Exception as e:
                    print(f"✗ 停止 {name} 失败: {e}")

        print("\n所有 Workers 已停止")

    return True


if __name__ == "__main__":
    success = start_workers()
    sys.exit(0 if success else 1)
