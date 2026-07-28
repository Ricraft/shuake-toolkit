"""Yatori and Autovisor launch preparation for the Web-only launcher."""

from __future__ import annotations

import locale
import os
import socket
import time


class CoreLaunchService:
    """Validate one core, prepare its environment, and submit its process."""

    def __init__(self, launcher):
        self.launcher = launcher

    def start_yatori(self) -> bool:
        launcher = self.launcher
        if launcher.running.get("yatori") or launcher.starting.get("yatori"):
            launcher.log_system("Yatori 已经在运行或启动中")
            return True
        if not launcher._get_runtime_coordinator().prepare_start("yatori"):
            return False

        launcher.yatori_path = launcher.find_yatori_path(launcher.get_base_dir())
        config_path = os.path.join(launcher.yatori_path, "config.yaml")
        if not os.path.exists(config_path):
            launcher.log_system(f"错误: 未找到配置文件 {config_path}")
            launcher._show_error(
                "启动失败",
                "未找到 config.yaml 配置文件\n请使用配置生成器创建配置",
            )
            return launcher._reject_runtime_start(
                "yatori",
                "未找到 Yatori 的 config.yaml，请先保存配置",
                failure_kind="configuration",
            )

        saved_config = launcher._load_yatori_config_data()
        runtime_validation_error = launcher._validate_yatori_runtime(
            saved_config.get("users") or [],
        )
        if runtime_validation_error:
            launcher.log_system(
                f"Yatori 启动已取消: {runtime_validation_error}"
            )
            return launcher._reject_runtime_start(
                "yatori",
                runtime_validation_error,
                failure_kind="configuration",
            )

        command, entry_path = launcher._get_yatori_command()
        if not command:
            launcher.log_system("错误: 未找到 Yatori 可执行文件")
            launcher._show_error("启动失败", "未找到 Yatori 可执行文件")
            return launcher._reject_runtime_start(
                "yatori",
                "未找到 Yatori 可执行文件，请检查核心是否安装完整",
            )

        launcher._sync_yatori_question_bank_url()
        if not launcher._claim_runtime_start("yatori"):
            if launcher._last_start_failure_kind.get("yatori") == "system":
                return False
            launcher.log_system("Yatori 已经在运行或启动中")
            return True

        launcher.log_system("正在启动 Yatori...")
        launcher.log_system(f"Yatori 入口: {entry_path}")
        return launcher._get_runtime_process_service().start(
            core="yatori",
            label="Yatori",
            command=command,
            cwd=launcher.yatori_path,
            encodings=launcher._build_encoding_candidates(
                "utf-8-sig",
                "utf-8",
                "gb18030",
                "gbk",
                "cp936",
            ),
        )

    def prepare_autovisor_question_bank(self) -> None:
        launcher = self.launcher
        if not launcher.question_bank.running:
            launcher.log_system("[Autovisor] 正在启动题库服务器...")
            launcher.start_question_bank(silent=True)
            for _ in range(30):
                if launcher.stop_requested.get("autovisor"):
                    return
                time.sleep(0.1)
        else:
            launcher.log_system("[Autovisor] 题库服务器已在运行")

        port = launcher.question_bank.port
        result = None
        for attempt in range(3):
            if launcher.stop_requested.get("autovisor"):
                return
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                result = sock.connect_ex(("127.0.0.1", port))
            if result == 0:
                launcher.log_system(
                    f"[Autovisor] 题库服务器验证成功 (尝试 {attempt + 1})"
                )
                return
            launcher.log_system(
                f"[Autovisor] 端口验证失败 (尝试 {attempt + 1}/3)，等待2秒..."
            )
            for _ in range(20):
                if launcher.stop_requested.get("autovisor"):
                    return
                time.sleep(0.1)
        launcher.log_system(
            f"[Autovisor] 警告: 题库服务器验证失败，错误码 {result}，继续启动..."
        )

    def build_autovisor_runtime_env(self):
        launcher = self.launcher
        environment = os.environ.copy()
        question_bank_url = launcher.get_question_bank_url()
        if question_bank_url:
            environment["QB_URL"] = question_bank_url
            launcher.log_system(
                f"[Autovisor] 设置题库URL: {question_bank_url}"
            )
        else:
            launcher.log_system(
                "[Autovisor] 警告: 题库服务器未启动，使用默认URL"
            )
        return environment

    def _prepare_autovisor_python_runtime(self, *, runtime_state):
        """Return ``(status, executable)`` after preparing script mode."""
        launcher = self.launcher
        python_executable = launcher.get_python_executable()
        if not python_executable:
            launcher.log_system("错误: 未找到 Python 解释器")
            launcher._show_error(
                "启动失败",
                "未找到 Python 解释器\n请确保已安装 Python 并添加到环境变量",
            )
            launcher._reject_runtime_start(
                "autovisor",
                "未找到可用的 Python 解释器",
            )
            return "failed", None

        missing_dependencies = launcher._check_autovisor_dependencies(
            python_executable
        )
        needs_browser = bool(runtime_state["needs_playwright_browser"])
        if not missing_dependencies and not needs_browser:
            return "ready", python_executable

        if missing_dependencies:
            package_names = ", ".join(
                sorted({item[1] for item in missing_dependencies})
            )
            launcher.log_system(
                "检测到当前 Python 环境缺少 Autovisor 依赖: "
                f"{package_names}"
            )
            for module_name, package_name, error in missing_dependencies:
                if error:
                    launcher.log_system(
                        f"依赖检查失败 [{module_name}/{package_name}]: {error}"
                    )
        if needs_browser:
            launcher.log_system(
                "未检测到可用 Chrome/Edge，将自动安装 Playwright Chromium "
                "作为浏览器回退。"
            )

        accepted = launcher._install_autovisor_dependencies_async(
            python_executable,
            missing_dependencies,
            ensure_playwright_browser=needs_browser,
        )
        if accepted or launcher.autovisor_installing:
            return "installing", python_executable
        launcher._reject_runtime_start(
            "autovisor",
            "Autovisor 依赖安装任务未能启动",
        )
        return "failed", None

    def start_autovisor(self) -> bool:
        launcher = self.launcher
        if not launcher._get_runtime_coordinator().prepare_start("autovisor"):
            return False
        if launcher.autovisor_installing:
            launcher.log_system(
                "Autovisor 依赖安装中，请等待安装完成后自动启动。"
            )
            return True
        if launcher.running.get("autovisor") or launcher.starting.get(
            "autovisor"
        ):
            launcher.log_system("Autovisor 已经在运行或启动中")
            return True

        launcher.autovisor_path = launcher.find_autovisor_path(
            launcher.get_base_dir()
        )
        multi_mode = launcher._get_autovisor_multi_mode()
        (
            script_path,
            script_name,
            exact_match,
            is_executable,
        ) = launcher._get_autovisor_entry_path(multi_mode)
        if not script_path:
            launcher.log_system(
                "错误: 未找到 Autovisor 入口文件，目录: "
                f"{launcher.autovisor_path}"
            )
            launcher._show_error(
                "启动失败",
                "Autovisor 目录中未找到可用入口文件\n"
                "请检查 Autovisor 包是否完整",
            )
            return launcher._reject_runtime_start(
                "autovisor",
                "未找到 Autovisor 入口文件，请检查核心是否安装完整",
            )

        config_path = os.path.join(launcher.autovisor_path, "configs.ini")
        if not os.path.exists(config_path):
            launcher.log_system(f"错误: 未找到配置文件 {config_path}")
            launcher._show_error(
                "启动失败",
                "未找到 configs.ini 配置文件\n请使用配置生成器创建配置",
            )
            return launcher._reject_runtime_start(
                "autovisor",
                "未找到 Autovisor 的 configs.ini，请先保存配置",
                failure_kind="configuration",
            )

        saved_config = launcher._load_autovisor_config_data()
        runtime_validation_error = launcher._validate_autovisor_runtime(
            saved_config.get("accounts") or [],
            multi_mode,
        )
        if runtime_validation_error:
            launcher.log_system(
                f"Autovisor 启动已取消: {runtime_validation_error}"
            )
            return launcher._reject_runtime_start(
                "autovisor",
                runtime_validation_error,
                failure_kind="configuration",
            )

        runtime_state = launcher._prepare_autovisor_config(
            config_path,
            multi_mode,
        )
        for summary in runtime_state["browser_summaries"]:
            launcher.log_system(summary)

        python_executable = None
        if is_executable:
            launcher.log_system(
                "检测到 Autovisor 可执行版，将直接启动 EXE。"
            )
        else:
            runtime_status, python_executable = (
                self._prepare_autovisor_python_runtime(
                    runtime_state=runtime_state,
                )
            )
            if runtime_status != "ready":
                return runtime_status == "installing"

        if not launcher._claim_runtime_start("autovisor"):
            if launcher._last_start_failure_kind.get("autovisor") == "system":
                return False
            launcher.log_system("Autovisor 已经在运行或启动中")
            return True

        launcher.log_system("正在启动 Autovisor...")
        launcher.log("autovisor", "正在启动任务...")
        launcher.log_system(f"Autovisor 目录: {launcher.autovisor_path}")
        launcher.log_system(f"Autovisor 入口: {script_name}")
        if is_executable:
            launcher.log_system("启动方式: 直接运行 EXE")
            launcher.log_system(
                "提示: 当前 Autovisor 为可执行版，已跳过 Python 依赖检查。"
            )
        else:
            launcher.log_system(f"Python 解释器: {python_executable}")
            if not exact_match:
                expected_name = (
                    "Autovisor_Multi.py" if multi_mode else "Autovisor.py"
                )
                launcher.log_system(
                    f"警告: 未找到请求入口 {expected_name}，"
                    f"已回落到 {script_name}"
                )

        command = (
            [script_path]
            if is_executable
            else [python_executable, script_path]
        )
        return launcher._get_runtime_process_service().start(
            core="autovisor",
            label="Autovisor",
            command=command,
            cwd=launcher.autovisor_path,
            encodings=launcher._build_encoding_candidates(
                locale.getpreferredencoding(False),
                "utf-8",
                "gb18030",
                "gbk",
            ),
            before_launch=self.prepare_autovisor_question_bank,
            env_factory=self.build_autovisor_runtime_env,
        )
