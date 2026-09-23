# encoding=utf-8
"""多账号进程编排器。

本模块只负责并发和进程生命周期。每个子进程都调用 ``Autovisor.run``，因此课程
识别、测试处理、视频设置和后续修复不会再出现两套实现。
"""

from __future__ import annotations

import multiprocessing
import importlib.util
import os
import sys
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from modules.multi_config import MultiAccountConfig


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def ensure_directories() -> None:
    for directory in (PROJECT_ROOT / "logs", PROJECT_ROOT / "res"):
        directory.mkdir(parents=True, exist_ok=True)


def run_single_account_process(config_path: str, account_id: int) -> None:
    """子进程入口；必须保持为模块级函数以兼容 Windows spawn。"""
    try:
        os.chdir(PROJECT_ROOT)
        os.environ["AUTOVISOR_ACCOUNT_ID"] = str(account_id)
        ensure_directories()

        # 延迟并按文件加载，规避 Autovisor 目录与 Autovisor.py 的同名歧义。
        project_root_text = str(PROJECT_ROOT)
        if project_root_text not in sys.path:
            sys.path.insert(0, project_root_text)
        spec = importlib.util.spec_from_file_location(
            "_autovisor_account_runtime", PROJECT_ROOT / "Autovisor.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError("无法加载 Autovisor.py")
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        exit_code = runtime.run(config_path=config_path, account_id=account_id)
        if exit_code:
            raise SystemExit(exit_code)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[账号{account_id}] 进程异常: {exc}", flush=True)
        traceback.print_exc()
        raise SystemExit(1) from exc


class MultiAccountManager:
    """以固定并发上限运行全部账号。"""

    def __init__(
        self,
        config_path: str = "configs.ini",
        *,
        process_context=None,
        poll_interval: float = 0.2,
        startup_delay: float = 2.0,
    ):
        self.config_path = str(Path(config_path).resolve())
        self.multi_config = MultiAccountConfig(self.config_path)
        self.processes: List[multiprocessing.Process] = []
        self._context = process_context or multiprocessing.get_context("spawn")
        self.poll_interval = max(0.0, poll_interval)
        self.startup_delay = max(0.0, startup_delay)

    def _start_account(self, account_id: int):
        process = self._context.Process(
            target=run_single_account_process,
            args=(self.config_path, account_id),
            name=f"Autovisor-Account-{account_id}",
        )
        process.start()
        self.processes.append(process)
        return process

    @staticmethod
    def _stop_processes(processes) -> None:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join()

    @staticmethod
    def _stop_processes_after_error(processes) -> None:
        """Best-effort cleanup without masking the orchestration error."""
        for process in processes:
            try:
                alive = process.is_alive()
            except Exception:
                # If liveness cannot be queried, try terminating it anyway.
                alive = True
            if alive:
                try:
                    process.terminate()
                except Exception:
                    # Continue so every other active child is also stopped.
                    pass
        for process in processes:
            try:
                process.join()
            except Exception:
                # Preserve the original exception while attempting every join.
                pass

    def run_all(self, max_concurrent: Optional[int] = None) -> Dict[int, int]:
        accounts = list(self.multi_config.accounts)
        if not accounts:
            print("未配置任何可运行账号。")
            return {}

        if max_concurrent is None:
            max_concurrent = len(accounts)
        if max_concurrent < 1:
            raise ValueError("max_concurrent 必须大于等于 1")
        max_concurrent = min(max_concurrent, len(accounts))

        print(f"共 {len(accounts)} 个账号，同时运行 {max_concurrent} 个")
        print("=" * 50)

        pending = deque(account.account_id for account in accounts)
        active = {}
        exit_codes: Dict[int, int] = {}

        try:
            while pending or active:
                while pending and len(active) < max_concurrent:
                    account_id = pending.popleft()
                    process = self._start_account(account_id)
                    active[process] = account_id
                    if self.startup_delay and (pending or len(active) < max_concurrent):
                        time.sleep(self.startup_delay)

                finished = [process for process in active if not process.is_alive()]
                for process in finished:
                    process.join()
                    account_id = active.pop(process)
                    exit_codes[account_id] = process.exitcode or 0
                    if process.exitcode:
                        print(f"[账号{account_id}] 异常退出，返回码: {process.exitcode}")

                if active and not finished:
                    time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n收到中断信号，正在停止所有账号...")
            self._stop_processes(list(active))
            for process, account_id in active.items():
                exit_codes[account_id] = process.exitcode or -1
        except Exception:
            self._stop_processes_after_error(list(active))
            raise

        print("=" * 50)
        print("所有账号运行完毕!")
        return exit_codes


if __name__ == "__main__":
    manager = MultiAccountManager("../configs.ini")
    manager.run_all()
