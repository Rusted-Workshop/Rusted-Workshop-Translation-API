"""
启动所有 Worker 进程
"""

import os
import signal
import subprocess
import sys
import time
from typing import Dict


def spawn_worker(command: list[str]) -> subprocess.Popen:
    """启动单个 worker 进程"""
    return subprocess.Popen(command, cwd=os.getcwd())


def stop_worker(name: str, process: subprocess.Popen) -> None:
    """停止单个 worker 进程"""
    if process.poll() is not None:
        return

    print(f"停止 {name} (PID: {process.pid})...")
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            process.send_signal(signal.SIGINT)

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


def start_workers():
    """启动所有 worker 进程"""
    print("=" * 60)
    print("启动翻译服务 Workers")
    print("=" * 60)

    # 读取配置
    file_worker_count = int(os.getenv("FILE_WORKER_COUNT", "15"))

    max_restarts = int(os.getenv("WORKER_MAX_RESTARTS", "0"))
    restart_delay = float(os.getenv("WORKER_RESTART_DELAY_SECONDS", "3"))
    process_specs: Dict[str, list[str]] = {}
    processes: Dict[str, subprocess.Popen] = {}
    restart_counts: Dict[str, int] = {}

    try:
        # 1. 启动协调器 Worker（1个）
        print("\n[1/2] 启动协调器 Worker...")
        coordinator_command = [sys.executable, "-m", "workers.coordinator_worker"]
        coordinator_process = spawn_worker(coordinator_command)
        process_specs["Coordinator Worker"] = coordinator_command
        processes["Coordinator Worker"] = coordinator_process
        restart_counts["Coordinator Worker"] = 0
        print(f"✓ 协调器 Worker 已启动 (PID: {coordinator_process.pid})")
        time.sleep(1)

        # 2. 启动文件翻译 Workers（多个）
        print(f"\n[2/2] 启动 {file_worker_count} 个文件翻译 Workers...")
        for i in range(file_worker_count):
            worker_name = f"File Worker #{i + 1}"
            worker_command = [sys.executable, "-m", "workers.file_translation_worker"]
            worker_process = spawn_worker(worker_command)
            process_specs[worker_name] = worker_command
            processes[worker_name] = worker_process
            restart_counts[worker_name] = 0
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
            for name, process in list(processes.items()):
                if process.poll() is not None:
                    print(f"\n⚠ {name} 意外退出 (退出码: {process.returncode})")
                    restart_counts[name] += 1

                    if max_restarts > 0 and restart_counts[name] > max_restarts:
                        print(f"✗ {name} 超过最大重启次数 {max_restarts}，停止监督")
                        return False

                    print(
                        f"↺ {restart_delay:g} 秒后重启 {name} "
                        f"(第 {restart_counts[name]} 次)"
                    )
                    time.sleep(restart_delay)
                    restarted_process = spawn_worker(process_specs[name])
                    processes[name] = restarted_process
                    print(f"✓ {name} 已重启 (PID: {restarted_process.pid})")

    except KeyboardInterrupt:
        print("\n\n收到停止信号，正在关闭所有 Workers...")

    finally:
        # 停止所有进程
        for name, process in processes.items():
            stop_worker(name, process)

        print("\n所有 Workers 已停止")

    return True


if __name__ == "__main__":
    success = start_workers()
    sys.exit(0 if success else 1)
