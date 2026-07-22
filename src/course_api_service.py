"""Web course-list APIs for Zhihuishu and Xuexitong accounts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

from src.course_catalog import (
    CourseCatalogError,
    normalize_account_index,
    parse_zhs_course_data,
)


class CourseAPIService:
    """Validate account selections and orchestrate course-list retrieval."""

    def __init__(self, launcher, *, process_factory=None):
        self.launcher = launcher
        self.process_factory = process_factory or subprocess.Popen
        self._fetch_lock = threading.Lock()
        self._xxt_fetch_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._active_process = None
        self._fetch_cancelled = threading.Event()

    def _set_active_process(self, process) -> None:
        with self._process_lock:
            self._active_process = process

    def _clear_active_process(self, process) -> None:
        with self._process_lock:
            if self._active_process is process:
                self._active_process = None

    def stop_active_fetch(self) -> bool:
        """Cancel an in-flight Zhihuishu fetch and close its browser process."""
        self._fetch_cancelled.set()
        with self._process_lock:
            process = self._active_process
        if process is None:
            return False
        try:
            if process.poll() is None:
                self.launcher._terminate_process_tree(process, "课程获取")
                return True
        except Exception as exc:
            self.launcher.log_system(f"[课程获取] 停止进程失败: {exc}")
        return False

    @staticmethod
    def _file_fingerprint(path: Path):
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return path.stat().st_size, digest.hexdigest()

    def _prepare_playwright(self, python_exe: str, base_dir: str) -> dict | None:
        try:
            available, detail = self.launcher._python_module_available(
                python_exe,
                "playwright",
            )
        except Exception as exc:
            return {
                "ok": False,
                "message": f"检查Playwright环境失败: {exc}",
            }
        if available:
            return None
        suffix = f" ({detail})" if detail else ""
        self.launcher.log_system(
            f"[课程获取] 检测到缺少playwright依赖，正在安装...{suffix}"
        )
        env = os.environ.copy()
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = (
            "https://npmmirror.com/mirrors/playwright"
        )
        env["PYTHONUNBUFFERED"] = "1"
        try:
            install_code = self.launcher._run_logged_command(
                [
                    python_exe,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-color",
                    "--prefer-binary",
                    "-i",
                    "https://pypi.tuna.tsinghua.edu.cn/simple",
                    "playwright>=1.52,<2",
                ],
                base_dir,
                env=env,
            )
            if install_code != 0:
                return {
                    "ok": False,
                    "message": f"安装playwright失败，返回码: {install_code}",
                }

            self.launcher.log_system(
                "[课程获取] 正在安装Playwright Chromium浏览器..."
            )
            install_code = self.launcher._run_logged_command(
                [python_exe, "-m", "playwright", "install", "chromium"],
                base_dir,
                env=env,
            )
            if install_code != 0:
                return {
                    "ok": False,
                    "message": f"安装Chromium浏览器失败，返回码: {install_code}",
                }
        except Exception as exc:
            return {"ok": False, "message": f"准备Playwright环境失败: {exc}"}
        self.launcher.log_system("[课程获取] Playwright环境准备完成")
        return None

    def _collect_process_output(self, process) -> list[str]:
        encodings = self.launcher._build_encoding_candidates("utf-8", "gbk")
        buffer = b""
        output = []

        def record(raw: bytes):
            line = self.launcher._decode_output_line(raw, encodings)
            cleaned = self.launcher._clean_log_text(line)
            if cleaned:
                self.launcher.log_system(f"[课程获取] {cleaned}")
                output.append(cleaned)

        while True:
            chunk = process.stdout.read(1)
            if not chunk:
                break
            buffer += chunk
            if chunk == b"\n":
                record(buffer)
                buffer = b""
        if buffer:
            record(buffer)
        return output

    def _run_zhihuishu_fetch(
        self,
        python_exe: str,
        script_path: Path,
        account_number: int,
        base_dir: str,
    ) -> dict | None:
        process = None
        try:
            if self._fetch_cancelled.is_set():
                return {"ok": False, "message": "课程获取已取消"}
            creationflags, startupinfo = (
                self.launcher._get_subprocess_window_kwargs()
            )
            env = os.environ.copy()
            env["PLAYWRIGHT_DOWNLOAD_HOST"] = (
                "https://npmmirror.com/mirrors/playwright"
            )
            process = self.process_factory(
                [python_exe, str(script_path), str(account_number)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=base_dir,
                text=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
                env=env,
            )
            self._set_active_process(process)
            if self._fetch_cancelled.is_set():
                self.stop_active_fetch()
                return {"ok": False, "message": "课程获取已取消"}
            output = self._collect_process_output(process)
            process.wait()
            if self._fetch_cancelled.is_set():
                return {"ok": False, "message": "课程获取已取消"}
            if process.returncode != 0:
                error_lines = output[-10:]
                error_detail = "\n".join(error_lines) if error_lines else "无输出"
                return {
                    "ok": False,
                    "message": (
                        f"脚本异常退出(返回码:{process.returncode})\n"
                        f"Python: {python_exe}\n最近输出:\n{error_detail}"
                    ),
                }
        except Exception as exc:
            if process is not None:
                try:
                    if process.poll() is None:
                        self.launcher._terminate_process_tree(process, "课程获取")
                except Exception as cleanup_exc:
                    self.launcher.log_system(
                        f"[课程获取] 异常进程清理失败: {cleanup_exc}"
                    )
            return {"ok": False, "message": f"运行脚本失败: {exc}"}
        finally:
            if process is not None:
                self._clear_active_process(process)
        return None

    def get_autovisor_courses(self, account_index=0, *, force_refresh=False) -> dict:
        if not self._fetch_lock.acquire(blocking=False):
            return {
                "ok": False,
                "message": "智慧树课程获取正在进行中，请稍候",
            }
        self._fetch_cancelled.clear()
        try:
            return self._get_autovisor_courses_locked(
                account_index,
                force_refresh=force_refresh,
            )
        finally:
            self._fetch_lock.release()

    def _get_autovisor_courses_locked(
        self,
        account_index=0,
        *,
        force_refresh=False,
    ) -> dict:
        try:
            account_index = normalize_account_index(account_index)
        except CourseCatalogError as exc:
            return {"ok": False, "message": str(exc)}
        accounts = self.launcher._load_autovisor_config_data().get("accounts") or []
        if account_index >= len(accounts):
            return {
                "ok": False,
                "message": f"智慧树账号索引 {account_index} 不存在",
            }
        account = accounts[account_index]
        account_number = self.launcher._as_int(
            account.get("account_id"),
            account_index + 1,
        )
        catalog_account_index = max(account_number - 1, 0)
        self.launcher.log_system(
            f"[课程获取] 正在获取账号配置 {account_number} 的课程..."
        )
        username = str(account.get("username", "")).strip()
        if not username:
            return {
                "ok": False,
                "message": f"智慧树账号配置 {account_number} 未配置用户名",
            }

        catalog = self.launcher._get_course_catalog_service()
        if not force_refresh:
            cached = catalog.get_cached("zhs", catalog_account_index, username)
            if cached is not None:
                self.launcher.log_system("[课程获取] 账号身份匹配，使用30分钟内缓存")
                return cached
        else:
            self.launcher.log_system("[课程获取] 用户主动刷新，跳过本地课程缓存")

        base_dir = self.launcher.get_base_dir()
        script_path = Path(base_dir) / "scripts" / "fetch_zhs_courses.py"
        if not script_path.is_file():
            return {"ok": False, "message": f"未找到课程获取脚本: {script_path}"}
        python_exe = self.launcher.get_python_executable()
        if not python_exe:
            return {"ok": False, "message": "未找到 Python 解释器"}

        preparation_failure = self._prepare_playwright(python_exe, base_dir)
        if preparation_failure:
            return preparation_failure
        if self._fetch_cancelled.is_set():
            return {"ok": False, "message": "课程获取已取消"}

        course_file = Path(base_dir) / "data" / "zhs_course.json"
        try:
            previous_fingerprint = self._file_fingerprint(course_file)
        except Exception as exc:
            return {"ok": False, "message": f"读取旧课程数据失败: {exc}"}

        process_failure = self._run_zhihuishu_fetch(
            python_exe,
            script_path,
            account_number,
            base_dir,
        )
        if process_failure:
            return process_failure
        if not course_file.is_file():
            return {"ok": False, "message": "未找到课程数据文件"}
        try:
            current_fingerprint = self._file_fingerprint(course_file)
        except Exception as exc:
            return {"ok": False, "message": f"读取课程数据失败: {exc}"}
        if previous_fingerprint == current_fingerprint:
            return {
                "ok": False,
                "message": "课程获取脚本未刷新数据文件，请检查登录或验证状态",
            }

        try:
            data = json.loads(course_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "message": f"读取课程数据失败: {exc}"}
        if not isinstance(data, dict):
            return {
                "ok": False,
                "message": "课程数据格式错误：顶层必须是账号对象",
            }
        if not isinstance(data.get(username), dict):
            self.launcher.log_system(
                f"[课程获取] 未找到账号 {username} 的数据，该账号可能未登录过"
            )
            return {
                "ok": False,
                "message": f"课程文件中没有账号 {username} 的有效数据，请重新登录",
            }
        try:
            courses, selected_identity = parse_zhs_course_data(data, username)
        except CourseCatalogError as exc:
            return {"ok": False, "message": str(exc)}
        if selected_identity:
            self.launcher.log_system(
                f"[课程获取] 使用账号 {selected_identity} 的课程数据"
            )
        result = {"ok": True, "courses": courses}
        try:
            catalog.put_cached(
                "zhs",
                catalog_account_index,
                selected_identity,
                result,
            )
        except Exception as exc:
            self.launcher.log_system(f"[课程获取] 缓存写入失败: {exc}")
        return result

    def get_xuexitong_courses(self, account_index=0, *, force_refresh=False) -> dict:
        if not self._xxt_fetch_lock.acquire(blocking=False):
            return {
                "ok": False,
                "message": "学习通课程获取正在进行中，请稍候",
            }
        try:
            return self._get_xuexitong_courses_locked(
                account_index,
                force_refresh=force_refresh,
            )
        finally:
            self._xxt_fetch_lock.release()

    def _get_xuexitong_courses_locked(
        self,
        account_index=0,
        *,
        force_refresh=False,
    ) -> dict:
        try:
            account_index = normalize_account_index(account_index)
        except CourseCatalogError as exc:
            return {"ok": False, "message": str(exc)}
        users = self.launcher._load_yatori_config_data().get("users", [])
        if account_index >= len(users):
            return {
                "ok": False,
                "message": (
                    f"账号索引 {account_index} 超出范围 (共 {len(users)} 个账号)"
                ),
            }
        user = users[account_index]
        account_type = str(user.get("accountType", "")).upper()
        if account_type != "XUEXITONG":
            return {
                "ok": False,
                "message": f"账号类型 {account_type} 不是学习通，无法获取课程",
            }
        username = str(user.get("account", "")).strip()
        password = str(user.get("password", "")).strip()
        self.launcher.log_system(
            f"[学习通课程] 正在获取账号 {account_index} 的课程..."
        )
        try:
            return self.launcher._get_course_catalog_service().get_xuexitong_courses(
                account_index,
                username,
                password,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            return {"ok": False, "message": f"获取学习通课程失败: {exc}"}
