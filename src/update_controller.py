"""Thread-safe core update checks and installation orchestration."""

from __future__ import annotations

import threading
from collections.abc import Callable


def _run_in_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


class UpdateController:
    """Coordinate Yatori and Autovisor checks without any UI dependency."""

    def __init__(
        self,
        core_manager,
        *,
        log: Callable[..., None],
        show_info: Callable[[str, str], None],
        show_warning: Callable[[str, str], None],
        show_error: Callable[[str, str], None],
        is_core_running: Callable[[str], bool],
        on_installed: Callable[[str], None],
        get_autovisor_version: Callable[[], str],
        schedule: Callable[[int, Callable[[], None]], None],
        run_async: Callable[[Callable[[], None]], None] = _run_in_background,
    ):
        self.core_manager = core_manager
        self.log = log
        self.show_info = show_info
        self.show_warning = show_warning
        self.show_error = show_error
        self.is_core_running = is_core_running
        self.on_installed = on_installed
        self.get_autovisor_version = get_autovisor_version
        self.schedule = schedule
        self.run_async = run_async

        self.yatori_update_info = None
        self.autovisor_update_info = None
        self.installing = False
        self.installing_core = None
        self.yatori_checking = False
        self.autovisor_checking = False
        self._lock = threading.RLock()

    def check_yatori_async(self, *, explicit: bool = False) -> bool:
        if not self.core_manager:
            if explicit:
                self.show_error("系统错误", "核心管理器初始化失败")
            return False
        with self._lock:
            if self.installing or self.yatori_checking:
                if explicit:
                    self.show_info("管理中心", "版本检查或安装正在进行中，请勿重复操作")
                return False
            self.yatori_checking = True

        self.log("手动触发版本检查..." if explicit else "正在检查 Yatori 更新...")

        def worker():
            result = None
            error = None
            try:
                result = self.core_manager.check_yatori_update()
            except Exception as exc:
                error = exc

            def finish():
                with self._lock:
                    self.yatori_checking = False
                if error:
                    message = f"检查 Yatori 更新失败: {error}"
                    if explicit:
                        self.show_error("管理中心", message)
                    else:
                        self.log(message)
                    return
                if explicit:
                    self.handle_explicit_yatori_result(result)
                else:
                    self._handle_automatic_yatori_result(result)

            self.schedule(0, finish)

        self.run_async(worker)
        return True

    def _handle_automatic_yatori_result(self, result) -> None:
        if not result:
            self.log("检查 Yatori 更新失败: 未获取到版本信息")
            return
        self.yatori_update_info = result
        if result.get("has_update") and result.get("installed"):
            version = (result.get("info") or {}).get("version", "未知")
            self.log(f"检测到 Yatori 新版本: {version}")
        elif not result.get("installed", False):
            self.log("未安装 Yatori，可在 Web 界面点击“安装 Yatori 更新”。")

    def handle_explicit_yatori_result(self, result) -> None:
        if not result:
            self.show_error("管理中心", "无法连接至 GitHub 节点，请检查网络环境。")
            return

        installed = result.get("installed", False)
        has_update = result.get("has_update", False)
        if not installed or has_update:
            self.yatori_update_info = result
            if installed:
                version = (result.get("info") or {}).get("version", "未知")
                self.log(f"检测到 Yatori 新版本 {version}，开始更新。")
            self.install_yatori_async(result.get("info"))
            return

        version = result.get("version", "未知")
        self.yatori_update_info = None
        self.show_info("管理中心", f"当前已是最新版本 ({version})")

    def install_yatori_async(self, release_info=None) -> bool:
        return self._install_async("yatori", release_info)

    def check_autovisor_async(self) -> bool:
        if not self.core_manager:
            self.show_error("提示", "核心管理器初始化失败，无法检查版本。")
            return False
        with self._lock:
            if self.installing or self.autovisor_checking:
                self.log("Autovisor 版本检查或安装正在进行中，请稍候。")
                return False
            self.autovisor_checking = True

        self.log("正在检查 Autovisor 最新版本...")

        def worker():
            result = None
            error = None
            try:
                result = self.core_manager.check_autovisor_update()
            except Exception as exc:
                error = exc

            def finish():
                with self._lock:
                    self.autovisor_checking = False
                if error:
                    self.log(f"Autovisor 版本检查失败: {error}")
                    self.show_error("Autovisor 版本", f"检查版本失败：\n{error}")
                elif not result:
                    self.log("Autovisor 版本检查失败: 未获取到版本信息")
                    self.show_error("Autovisor 版本", "未能获取最新版本信息，请稍后重试。")
                else:
                    self.handle_autovisor_result(result)

            self.schedule(0, finish)

        self.run_async(worker)
        return True

    def handle_autovisor_result(self, result) -> None:
        release_info = result.get("info") or result
        latest_version = release_info.get("version", "未知")
        current_version = result.get("version") or self.get_autovisor_version()
        has_update = result.get("has_update", False)
        installed = result.get("installed", True)
        self.log(f"Autovisor 最新版本: {latest_version}")

        if not has_update:
            self.autovisor_update_info = None
            self.show_info(
                "Autovisor 版本",
                f"当前已是最新版本。\n\n当前版本: {current_version}",
            )
            return

        self.autovisor_update_info = result
        message = (
            f"检测到 Autovisor 新版本: {latest_version}\n\n"
            f"当前版本: {current_version if installed else '未安装'}"
        )
        self.log(message.replace("\n", " "))
        self.log("Web UI 已记录更新信息，可通过 install_autovisor_update 动作触发安装。")

    def install_autovisor_async(self, release_info=None) -> bool:
        return self._install_async("autovisor", release_info)

    def _install_async(self, core: str, release_info=None) -> bool:
        label = "Yatori" if core == "yatori" else "Autovisor"
        if not self.core_manager:
            self.show_error("系统错误", "核心管理器初始化失败")
            return False
        if self.is_core_running(core):
            self.show_warning(f"{label} 更新", f"请先停止 {label}，再执行核心更新。")
            return False

        cached = (
            self.yatori_update_info
            if core == "yatori"
            else self.autovisor_update_info
        )
        release_info = release_info or (cached or {}).get("info")
        if not release_info:
            self.show_warning(f"{label} 更新", f"暂无可安装的 {label} 版本信息，请先检查更新。")
            return False

        with self._lock:
            if self.installing or self.yatori_checking or self.autovisor_checking:
                self.show_info("管理中心", "版本检查或安装正在进行中，请勿重复操作")
                return False
            self.installing = True
            self.installing_core = core

        self.log(f"开始安装 {label} {release_info.get('version', 'unknown')}...")

        def on_progress(info):
            progress = info.get("progress", 0) or 0
            downloaded = info.get("downloaded") or 0
            total = info.get("total")
            if total:
                self.log(
                    f"{label} 下载中: {progress:.1f}% ({downloaded}/{total} bytes)",
                    replace_last=True,
                )
            else:
                self.log(f"{label} 下载中: {downloaded} bytes", replace_last=True)

        def worker():
            success = False
            error = None
            try:
                installer = getattr(self.core_manager, f"install_{core}")
                success = bool(installer(release_info, on_progress))
            except Exception as exc:
                error = exc

            def finish():
                with self._lock:
                    self.installing = False
                    self.installing_core = None
                if success:
                    if core == "yatori":
                        self.yatori_update_info = None
                    else:
                        self.autovisor_update_info = None
                    self.on_installed(core)
                    self.log(f"✅ {label} 核心处理完成")
                    self.show_info(f"{label} 更新", f"{label} 核心已成功安装/更新。")
                else:
                    message = str(error) if error else "下载或安装失败"
                    self.log(f"❌ {label} 安装失败: {message}")
                    self.show_error(
                        f"{label} 更新",
                        f"{message}\n\n请检查网络设置或稍后重试。",
                    )

            self.schedule(0, finish)

        self.run_async(worker)
        return True
