"""Thread-safe core update checks and installation orchestration."""

from __future__ import annotations

import copy
import secrets
import threading
from collections.abc import Callable


def _run_in_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


# --------------------------------------------------------------------------- #
# Autovisor 本地适配版政策
#
# 项目内的 Autovisor 是被魔改过的本地适配版，上游发行包未经过适配，直接覆盖会
# 破坏适配逻辑。因此 Autovisor 只提供「版本信息展示」，不提供任何安装入口，
# 并在版本说明前固定展示下面的声明与原作者下载引导。
# --------------------------------------------------------------------------- #

AUTOVISOR_ADAPTED_NOTICE = (
    "声明：由于账号样本缺失及时间问题，该启动器版本无法保证能正常处理 zhs 的课程；"
    "同时 zhs 页面更新，虽然老版本我修改了对应登录页，但是自己账号没有课程视频了，"
    "而且相应题库接口等待更换。下方的更新版本为原作者版本（未经软件适配，会有一些"
    "兼容性问题），此处暂不支持直接下载。若本软件旧核心无法支持正常刷课，"
    "请下载下方原作者最新软件："
)

AUTOVISOR_UPSTREAM_LINKS = (
    {
        "key": "autovisor_github",
        "label": "GitHub（推荐，给原作者一个 star）",
        "url": "https://github.com/CXRunfree/Autovisor/releases",
    },
    {
        "key": "autovisor_lanzou",
        "label": "蓝奏云（国内网络推荐）",
        "url": "https://wwk.lanzouj.com/b05evsxif",
        "password": "492l",
    },
)

AUTOVISOR_UPSTREAM_LINK_MAP = {
    item["key"]: item["url"] for item in AUTOVISOR_UPSTREAM_LINKS
}

AUTOVISOR_INSTALL_DISABLED_REASON = (
    "当前 Autovisor 为项目本地适配版（已魔改），本页面只做版本信息展示，"
    "不提供直接覆盖安装。"
)


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
        self._yatori_confirmation = None
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
            current_version = result.get("version", "未知")
            self.log(f"检测到 Yatori 新版本: {version}（当前版本: {current_version}）")
            self._log_yatori_release_notes(result.get("info"))
        elif not result.get("installed", False):
            self.log("未安装 Yatori，可在 Web 界面点击“安装 Yatori 更新”。")
            self._log_yatori_release_notes(result.get("info"))

    def _format_yatori_release_notes(self, release_info, *, max_chars: int = 900) -> str:
        body = str((release_info or {}).get("body") or "").strip()
        if not body or body == "通过备用方式获取":
            return ""

        lines = []
        for raw_line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("<!--") and line.endswith("-->"):
                continue
            lines.append(line)
            if len(lines) >= 14:
                break

        summary = "\n".join(lines).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip() + "..."
        return summary

    def _log_yatori_release_notes(self, release_info) -> None:
        notes = self._format_yatori_release_notes(release_info)
        if notes:
            self.log(f"Yatori 最新版本介绍:\n{notes}")

    def _build_yatori_current_message(self, result) -> str:
        version = result.get("version", "未知")
        if result.get("local_newer"):
            remote_version = (result.get("info") or {}).get("version", "未知")
            return (
                f"本地 Yatori 版本 ({version}) 高于当前远端版本 "
                f"({remote_version})，已跳过降级。"
            )
        message = f"当前已是最新版本 ({version})"
        notes = self._format_yatori_release_notes(result.get("info"))
        if notes:
            message = f"{message}\n\n最新版本介绍:\n{notes}"
        return message

    def _build_yatori_update_dialog(self, result, confirmation_token):
        release_info = result.get("info") or {}
        latest_version = release_info.get("version", "未知")
        installed = result.get("installed", False)
        current_version = result.get("version") or ("未安装" if not installed else "未知")
        notes = self._format_yatori_release_notes(release_info)
        return {
            "core": "yatori",
            "installed": installed,
            "currentVersion": current_version,
            "latestVersion": latest_version,
            "assetName": release_info.get("asset_name") or "未知资源",
            "publishedAt": release_info.get("published_at") or "",
            "releaseNotes": notes or "该版本暂未提供更新说明。",
            "releaseNotesAvailable": bool(notes),
            "confirmationToken": confirmation_token,
        }

    def _prepare_yatori_update_confirmation_result(self, result):
        if not result:
            with self._lock:
                self._yatori_confirmation = None
            message = "无法连接至 GitHub 节点，请检查网络环境。"
            self.show_error("管理中心", message)
            return {"ok": False, "message": message}

        installed = result.get("installed", False)
        has_update = result.get("has_update", False)
        if not installed or has_update:
            release_info = result.get("info") or {}
            confirmation_token = secrets.token_urlsafe(24)
            with self._lock:
                self.yatori_update_info = result
                self._yatori_confirmation = {
                    "token": confirmation_token,
                    "release_info": copy.deepcopy(release_info),
                }
            dialog = self._build_yatori_update_dialog(
                result,
                confirmation_token,
            )
            if installed:
                self.log(
                    "检测到 Yatori 新版本 "
                    f"{dialog['latestVersion']}（当前版本: {dialog['currentVersion']}），"
                    "等待用户确认更新。"
                )
            else:
                self.log(f"检测到 Yatori 未安装，等待用户确认安装 {dialog['latestVersion']}。")
            return {
                "ok": True,
                "updateDialog": dialog,
                "toast": "已获取 Yatori 更新说明，请确认是否更新",
                "toastType": "info",
            }

        with self._lock:
            self.yatori_update_info = None
            self._yatori_confirmation = None
        message = self._build_yatori_current_message(result)
        self.show_info("管理中心", message)
        return {
            "ok": True,
            "upToDate": True,
            "localNewer": bool(result.get("local_newer")),
            "message": message,
            "toast": (
                "本地版本高于远端，已跳过降级"
                if result.get("local_newer")
                else f"当前已是最新版本 ({result.get('version', '未知')})"
            ),
            "toastType": "info",
        }

    def prepare_yatori_update_confirmation(self):
        if not self.core_manager:
            message = "核心管理器初始化失败"
            self.show_error("系统错误", message)
            return {"ok": False, "message": message}

        with self._lock:
            if self.installing or self.yatori_checking:
                message = "版本检查或安装正在进行中，请勿重复操作"
                self.show_info("管理中心", message)
                return {"ok": False, "message": message}
            self.yatori_checking = True

        self.log("手动触发版本检查...")
        try:
            result = self.core_manager.check_yatori_update()
        except Exception as exc:
            message = f"检查 Yatori 更新失败: {exc}"
            self.show_error("管理中心", message)
            return {"ok": False, "message": message}
        finally:
            with self._lock:
                self.yatori_checking = False

        return self._prepare_yatori_update_confirmation_result(result)

    def handle_explicit_yatori_result(self, result) -> None:
        self._prepare_yatori_update_confirmation_result(result)

    def install_yatori_async(self, release_info=None) -> bool:
        return self._install_async("yatori", release_info)

    def install_yatori_confirmed_async(self, confirmation_token) -> bool:
        with self._lock:
            confirmation = self._yatori_confirmation
            expected_token = (confirmation or {}).get("token")
            if (
                not isinstance(confirmation_token, str)
                or not confirmation_token
                or not isinstance(expected_token, str)
                or not secrets.compare_digest(
                    confirmation_token,
                    expected_token,
                )
            ):
                self.show_warning(
                    "Yatori 更新",
                    "更新确认已失效，请重新点击更新按钮并核对版本说明。",
                )
                return False

            release_info = copy.deepcopy(confirmation["release_info"])
            accepted = self._install_async("yatori", release_info)
            if accepted:
                self._yatori_confirmation = None
            return accepted

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

    def _build_autovisor_update_dialog(self, result):
        """Read-only Autovisor dialog: version info + notice + upstream links.

        Deliberately carries no confirmation token: there is no install path
        for the locally adapted core.
        """
        release_info = result.get("info") or {}
        installed = result.get("installed", True)
        notes = self._format_yatori_release_notes(release_info)
        current = result.get("version") or self.get_autovisor_version()
        latest = release_info.get("version", "未知")
        return {
            "core": "autovisor",
            "installed": installed,
            "currentVersion": current,
            "latestVersion": latest,
            "assetName": release_info.get("asset_name") or "未知资源",
            "publishedAt": release_info.get("published_at") or "",
            "releaseNotes": notes or "该版本暂未提供更新说明。",
            "releaseNotesAvailable": bool(notes),
            "notice": AUTOVISOR_ADAPTED_NOTICE,
            "downloadLinks": [dict(item) for item in AUTOVISOR_UPSTREAM_LINKS],
            "installDisabled": True,
            "installDisabledReason": AUTOVISOR_INSTALL_DISABLED_REASON,
            "summary": (
                f"原作者最新版本 {latest}（本地适配版 {current}）。"
                "本页面仅作版本信息展示，不支持直接下载或覆盖安装。"
            ),
        }

    def show_autovisor_update_dialog(self):
        """Synchronous Autovisor version dialog used by the About page button."""
        if not self.core_manager:
            message = "核心管理器初始化失败，无法检查 Autovisor 版本。"
            self.show_error("Autovisor 版本", message)
            return {"ok": False, "message": message}

        with self._lock:
            if self.autovisor_checking or self.installing:
                message = "Autovisor 版本检查或安装正在进行中，请稍后重试。"
                self.log(message)
                return {"ok": False, "message": message}
            self.autovisor_checking = True

        self.log("手动检查 Autovisor 版本...")
        try:
            result = self.core_manager.check_autovisor_update()
        except Exception as exc:
            message = f"检查 Autovisor 版本失败: {exc}"
            self.log(message)
            self.show_error("Autovisor 版本", message)
            return {"ok": False, "message": message}
        finally:
            with self._lock:
                self.autovisor_checking = False

        if not result:
            message = "未能获取 Autovisor 最新版本信息，请检查网络后重试。"
            self.log(message)
            self.show_error("Autovisor 版本", message)
            return {"ok": False, "message": message}

        self.autovisor_update_info = result
        dialog = self._build_autovisor_update_dialog(result)
        self.log(
            f"Autovisor 最新版本: {dialog['latestVersion']}"
            f"（本地适配版 {dialog['currentVersion']}）；"
            "仅展示版本信息，不提供直接覆盖安装。"
        )
        return {
            "ok": True,
            "updateDialog": dialog,
            "toast": "已获取 Autovisor 版本信息（本地适配版，不提供直接安装）",
            "toastType": "info",
        }

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
        self.log(
            "Autovisor 当前为项目本地适配版；版本信息仅供参考，"
            "Web UI 不会用上游包覆盖安装。"
        )

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
