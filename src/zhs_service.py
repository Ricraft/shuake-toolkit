"""ZHS（智慧树）第三核心服务层。

职责：
- 路径解析：源码目录 zhs/、独立解释器 .runtime/venv-zhs、账号数据目录 data/zhs/account-<id>/.zhs
- 运行环境自检（venv-zhs 是否装好 zhs 与依赖）
- 配置读写：启动器 UI 字段 <-> zhs config.toml（只写托管字段，未托管字段原样保留）
- 扫码登录：调用 src/zhs_bridge/qr_login.py 子进程，把二维码与扫码状态回传给 UI
- 课程列表：运行 zhs fetch，读取 .zhs/execution.json 并归一化
- 运行命令构造：play / homework / exam

说明：zhs 的所有数据都写在「当前工作目录/.zhs」下，因此每个账号使用独立工作目录，
启动子进程时 cwd 固定为账号目录，实现账号间隔离（单账号串行，并发留到后续版本）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ACCOUNT_ID_DEFAULT = 1
ENV_CACHE_SECONDS = 20.0
FETCH_TIMEOUT_SECONDS = 300
HOMEWORK_TIMEOUT_SECONDS = 300
LOGIN_TIMEOUT_SECONDS = 600
SESSION_CHECK_TIMEOUT_SECONDS = 40
# 登录态校验结果缓存：ok 有效期长一些，过期/不可用较短，避免每次轮询都打网络
SESSION_CACHE_SECONDS_OK = 300.0
SESSION_CACHE_SECONDS_SHORT = 30.0

# 只用于探测依赖，不参与业务
IMPORT_PROBE_SCRIPT = (
    "import zhs, httpx, pydantic, typer, loguru, Crypto, openai, tiktoken, PIL, qrcode, tomli_w, pptx, bs4; "
    "print(getattr(zhs, '__version__', 'unknown'))"
)


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "y"):
            return True
        if text in ("0", "false", "no", "off", "n", ""):
            return False
    return default


def _to_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    if minimum is not None and number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def _to_float(
    value: Any,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def _to_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    raise TypeError(f"不支持的 TOML 值类型: {type(value)!r}")


def dump_toml(data: Mapping[str, Any]) -> str:
    """把扁平/一层嵌套的 dict 写成 TOML 文本（够用即可，不处理深层嵌套）。"""
    lines: list[str] = []
    tables: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, Mapping):
            tables.append((key, value))
        else:
            lines.append(f"{key} = {_toml_scalar(value)}")
    for name, table in tables:
        if not table:
            continue
        lines.append("")
        lines.append(f"[{name}]")
        for key, value in table.items():
            if isinstance(value, Mapping):
                continue
            lines.append(f"{key} = {_toml_scalar(value)}")
    return "\n".join(lines) + "\n"


class ZhsService:
    """ZHS 核心的服务门面，所有方法都可从 web 层直接调用。"""

    def __init__(self, launcher: Any) -> None:
        self.launcher = launcher
        self._env_cache: tuple[float, dict[str, Any]] | None = None
        self._env_lock = threading.Lock()
        self._login_lock = threading.Lock()
        self._closing = False
        self._logins: dict[int, dict[str, Any]] = {}
        self._fetch_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._session_cache: dict[int, dict[str, Any]] = {}
        self._session_pending: set[int] = set()

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------
    def base_dir(self) -> str:
        getter = getattr(self.launcher, "get_base_dir", None)
        if callable(getter):
            return str(getter())
        return str(Path(__file__).resolve().parent.parent)

    def source_dir(self) -> str:
        return os.path.join(self.base_dir(), "zhs")

    def venv_dir(self) -> str:
        return os.path.join(self.base_dir(), ".runtime", "venv-zhs")

    def venv_python(self) -> str:
        return os.path.join(self.venv_dir(), "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")

    def bridge_script(self) -> str:
        return os.path.join(self.base_dir(), "src", "zhs_bridge", "qr_login.py")

    @staticmethod
    def normalize_account_id(account_id: Any) -> int:
        return _to_int(account_id, ACCOUNT_ID_DEFAULT, minimum=1)

    def account_dir(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        account = self.normalize_account_id(account_id)
        return os.path.join(self.base_dir(), "data", "zhs", f"account-{account}")

    def data_dir(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        return os.path.join(self.account_dir(account_id), ".zhs")

    def config_path(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        return os.path.join(self.data_dir(account_id), "config.toml")

    def cookies_path(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        return os.path.join(self.data_dir(account_id), "cookies.json")

    def execution_path(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        return os.path.join(self.data_dir(account_id), "execution.json")

    def qr_image_path(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        return os.path.join(self.data_dir(account_id), "qrcode.png")

    def log_dir(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> str:
        return os.path.join(self.data_dir(account_id), "logs")

    def ensure_account_dirs(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> None:
        Path(self.data_dir(account_id)).mkdir(parents=True, exist_ok=True)
        Path(self.log_dir(account_id)).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 运行环境
    # ------------------------------------------------------------------
    def environment_state(self, *, refresh: bool = False) -> dict[str, Any]:
        """检查 venv-zhs 是否可用（带 20 秒缓存，避免状态轮询反复起子进程）。"""
        now = time.monotonic()
        if not refresh and self._env_cache is not None:
            stamp, cached = self._env_cache
            if now - stamp < ENV_CACHE_SECONDS:
                return dict(cached)
        with self._env_lock:
            if not refresh and self._env_cache is not None:
                stamp, cached = self._env_cache
                if now - stamp < ENV_CACHE_SECONDS:
                    return dict(cached)
            result = self._probe_environment()
            self._env_cache = (time.monotonic(), dict(result))
            return result

    def _probe_environment(self) -> dict[str, Any]:
        python = self.venv_python()
        state: dict[str, Any] = {
            "ready": False,
            "python": python,
            "version": "",
            "message": "",
            "checked_at": time.time(),
        }
        if not os.path.isfile(python):
            state["message"] = "未找到 ZHS 运行环境（.runtime/venv-zhs），点击扫码登录或启动核心可自动准备"
            return state
        self.ensure_account_dirs()
        probe = self._run_command(
            [python, "-c", IMPORT_PROBE_SCRIPT],
            cwd=self.account_dir(),
            timeout=60,
        )
        if probe["code"] != 0:
            tail = (probe["stderr"] or probe["stdout"] or "").strip().splitlines()
            state["message"] = "ZHS 依赖不完整：" + (tail[-1] if tail else "未知错误")
            return state
        state["ready"] = True
        state["version"] = (probe["stdout"] or "").strip().splitlines()[-1] if probe["stdout"].strip() else ""
        return state

    def _run_command(
        self,
        command: Sequence[str],
        *,
        cwd: str,
        timeout: int,
        env_extra: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if env_extra:
            env.update({key: str(value) for key, value in env_extra.items()})
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                list(command),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            return {"code": -1, "stdout": "", "stderr": f"命令执行超时（{timeout} 秒）", "timed_out": True}
        except OSError as exc:
            return {"code": -1, "stdout": "", "stderr": f"命令启动失败：{exc}", "timed_out": False}
        return {
            "code": completed.returncode,
            "stdout": self._decode(completed.stdout),
            "stderr": self._decode(completed.stderr),
            "timed_out": False,
        }

    @staticmethod
    def _decode(raw: bytes | None) -> str:
        if not raw:
            return ""
        for encoding in ("utf-8", "gb18030", "cp936"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def default_settings(self) -> dict[str, Any]:
        """与 zhs AppConfig 默认值一致的托管字段（缺失项由 zhs 自己补默认值）。"""
        return {
            "limit": 0,
            "threshold": 0.91,
            "video": {
                "zhidao_speed": 1.5,
                "hike_speed": 1.25,
                "ai_speed": 1.5,
                "ai_learn_optional": False,
                "ai_threshold": 90,
            },
            "homework": {
                "threshold": 100,
                "max_submit": 0,
                "delay_min": 1.0,
                "delay_max": 2.0,
                "ai_homework_threshold": 90,
                "skip_ai_free_exam": True,
            },
            "ai": {
                "enabled": True,
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "max_token": 27900,
                "stream_timeout": 120.0,
                "stream_idle_timeout": 30.0,
            },
            "question_bank": {
                "enabled": False,
                "token": "",
                "query_url": "https://tk.enncy.cn/query",
                "info_url": "https://tk.enncy.cn/info",
                "scopes": ["zhidao_exam", "ai_exam"],
            },
            "exam": {"save_nums": 5, "delay_min": 3.0, "delay_max": 5.0},
            "proxies": {"http": "", "https": ""},
            "display": {"log_level": "INFO"},
            "qr": {"image_path": ""},
        }

    def load_settings(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        defaults = self.default_settings()
        path = self.config_path(account_id)
        try:
            with open(path, "rb") as handle:
                raw = tomllib.load(handle)
        except FileNotFoundError:
            return defaults
        except (tomllib.TOMLDecodeError, OSError) as exc:
            self._log(f"读取 ZHS 配置失败，已回退默认值：{exc}")
            return defaults
        merged = _deep_merge(defaults, raw)
        merged["qr"]["image_path"] = self.qr_image_path(account_id)
        return merged

    def save_settings(self, payload: Mapping[str, Any] | None, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        current = self.load_settings(account_id)
        normalized = self._normalize_settings(payload or {})
        merged = _deep_merge(current, normalized)
        merged["qr"] = {"image_path": self.qr_image_path(account_id)}
        self.ensure_account_dirs(account_id)
        text = dump_toml(merged)
        path = self.config_path(account_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
        return merged

    def _normalize_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if "limit" in payload:
            result["limit"] = _to_int(payload.get("limit"), 0, minimum=0)
        if "threshold" in payload:
            raw = payload.get("threshold")
            value = _to_float(raw, 0.91)
            if value > 1.0:
                value = value / 100.0
            result["threshold"] = round(min(max(value, 0.0), 1.0), 4)

        video = payload.get("video") or {}
        if isinstance(video, Mapping) and video:
            section: dict[str, Any] = {}
            for key in ("zhidao_speed", "hike_speed", "ai_speed"):
                if key in video:
                    section[key] = round(_to_float(video.get(key), 1.5, minimum=1.0, maximum=2.0), 2)
            if "ai_learn_optional" in video:
                section["ai_learn_optional"] = _to_bool(video.get("ai_learn_optional"))
            if "ai_threshold" in video:
                section["ai_threshold"] = _to_int(video.get("ai_threshold"), 90, minimum=0, maximum=100)
            result["video"] = section

        homework = payload.get("homework") or {}
        if isinstance(homework, Mapping) and homework:
            section = {}
            if "threshold" in homework:
                section["threshold"] = _to_int(homework.get("threshold"), 100, minimum=0, maximum=100)
            if "max_submit" in homework:
                section["max_submit"] = _to_int(homework.get("max_submit"), 0, minimum=0)
            result["homework"] = section

        ai = payload.get("ai") or {}
        if isinstance(ai, Mapping) and ai:
            section = {}
            if "enabled" in ai:
                section["enabled"] = _to_bool(ai.get("enabled"), True)
            if "api_key" in ai:
                section["api_key"] = _to_text(ai.get("api_key"))
            if "base_url" in ai:
                section["base_url"] = _to_text(ai.get("base_url"), "https://api.openai.com/v1")
            if "model" in ai:
                section["model"] = _to_text(ai.get("model"), "gpt-4o-mini")
            if "max_token" in ai:
                section["max_token"] = _to_int(ai.get("max_token"), 27900, minimum=1024)
            result["ai"] = section

        bank = payload.get("question_bank") or {}
        if isinstance(bank, Mapping) and bank:
            section = {}
            if "enabled" in bank:
                section["enabled"] = _to_bool(bank.get("enabled"))
            if "token" in bank:
                section["token"] = _to_text(bank.get("token"))
            result["question_bank"] = section

        proxies = payload.get("proxies") or {}
        if isinstance(proxies, Mapping) and proxies:
            result["proxies"] = {
                "http": _to_text(proxies.get("http")),
                "https": _to_text(proxies.get("https")),
            }
        return result

    # ------------------------------------------------------------------
    # 扫码登录
    # ------------------------------------------------------------------
    def _login_entry(self, account_id: int) -> dict[str, Any]:
        entry = self._logins.get(account_id)
        if entry is None:
            entry = {
                "status": "idle",
                "message": "",
                "image": "",
                "image_path": self.qr_image_path(account_id),
                "started_at": 0.0,
                "updated_at": 0.0,
                "process": None,
                "thread": None,
                "cancelled": False,
            }
            self._logins[account_id] = entry
        return entry

    def login_snapshot(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        account = self.normalize_account_id(account_id)
        entry = self._login_entry(account)
        return {
            "status": entry["status"],
            "message": entry["message"],
            "image": entry["image"],
            "image_path": entry["image_path"],
            "running": entry["status"] in ("starting", "waiting", "scanned"),
            "started_at": entry["started_at"],
            "updated_at": entry["updated_at"],
            "session": self.session_state(account),
        }

    def start_login(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        account = self.normalize_account_id(account_id)
        entry = self._login_entry(account)
        with self._login_lock:
            if self._closing:
                return {"ok": False, "message": "启动器正在退出，无法开始扫码登录"}
            thread = entry.get("thread")
            if thread is not None and thread.is_alive():
                return {"ok": False, "message": "扫码登录已在进行中", "snapshot": self.login_snapshot(account)}
            env = self.environment_state()
            if not env.get("ready"):
                return {"ok": False, "message": env.get("message") or "ZHS 运行环境不可用", "snapshot": self.login_snapshot(account)}
            self.ensure_account_dirs(account)
            entry.update({
                "status": "starting",
                "message": "正在获取二维码…",
                "image": "",
                "image_path": self.qr_image_path(account),
                "started_at": time.time(),
                "updated_at": time.time(),
                "cancelled": False,
                "process": None,
            })
            thread = threading.Thread(target=self._login_worker, args=(account,), name=f"zhs-login-{account}", daemon=True)
            entry["thread"] = thread
            thread.start()
        self._log("开始扫码登录，正在获取二维码…")
        return {"ok": True, "message": "已开始获取二维码", "snapshot": self.login_snapshot(account)}

    def shutdown(self) -> None:
        """Stop QR workers on exit without clearing saved account credentials."""
        with self._login_lock:
            self._closing = True
            processes = []
            for entry in self._logins.values():
                entry["cancelled"] = True
                if entry["status"] in ("starting", "waiting", "scanned"):
                    entry.update({"status": "cancelled", "message": "启动器已退出，扫码登录已停止"})
                if entry.get("process") is not None:
                    processes.append(entry["process"])
        for process in processes:
            self._terminate(process)

    def cancel_login(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        account = self.normalize_account_id(account_id)
        entry = self._login_entry(account)
        # 先落状态再杀进程，避免 worker 抢先写入 error。
        if entry["status"] in ("starting", "waiting", "scanned"):
            entry["status"] = "cancelled"
            entry["message"] = "已取消扫码登录"
            entry["updated_at"] = time.time()
        entry["cancelled"] = True
        process = entry.get("process")
        if process is not None and process.poll() is None:
            self._terminate(process)
        # 用户点「取消登录」通常是想换一个账号：旧凭证必须一并清掉，否则界面会继续显示已登录。
        cleaned = self.clear_credentials(account)
        if entry["status"] in ("success", "idle"):
            entry.update({"status": "cancelled", "message": "已取消扫码登录，并清除旧登录凭证", "updated_at": time.time()})
        elif entry["message"]:
            entry["message"] = entry["message"] + "，旧登录凭证已清除"
        snapshot = self.login_snapshot(account)
        snapshot["cleared"] = bool(cleaned.get("removed"))
        return {"ok": True, "message": snapshot.get("message") or "已取消扫码登录", "snapshot": snapshot}

    def _login_worker(self, account: int) -> None:
        entry = self._login_entry(account)
        python = self.venv_python()
        command = [python, self.bridge_script(), self.qr_image_path(account)]
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = None
        watchdog = None
        stderr_tail: list[str] = []
        try:
            with self._login_lock:
                if self._closing or entry["cancelled"]:
                    return
                process = subprocess.Popen(
                    command,
                    cwd=self.account_dir(account),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **kwargs,
                )
                entry["process"] = process
            watchdog = threading.Timer(LOGIN_TIMEOUT_SECONDS, self._terminate, args=(process,))
            watchdog.daemon = True
            watchdog.start()
            assert process.stdout is not None
            for raw in process.stdout:
                line = self._decode(raw).strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._apply_login_event(account, entry, event)
            assert process.stderr is not None
            stderr_tail = [self._decode(chunk).strip() for chunk in process.stderr.readlines()]
            code = process.wait()
            if entry["cancelled"]:
                entry["status"] = "cancelled"
                entry["message"] = entry["message"] or "已取消扫码登录"
                return
            if entry["status"] not in ("success", "error"):
                detail = ""
                for item in reversed(stderr_tail):
                    if item:
                        detail = item.splitlines()[-1][:300]
                        break
                if code == 0:
                    entry.update({"status": "error", "message": "扫码登录未完成（进程已退出）"})
                else:
                    entry.update({"status": "error", "message": "扫码登录失败：" + (detail or ("返回码 " + str(code)))})
        except Exception as exc:
            entry.update({"status": "error", "message": f"扫码登录异常：{exc}"})
        finally:
            if watchdog is not None:
                watchdog.cancel()
            entry["process"] = None
            entry["updated_at"] = time.time()
            if entry["status"] == "success":
                self._log("ZHS 登录成功，Cookie 已保存")
            elif entry["status"] == "error":
                self._log(f"ZHS 登录失败：{entry['message']}")
            if process is not None and process.poll() is None:
                self._terminate(process)

    def _apply_login_event(self, account: int, entry: dict[str, Any], event: Mapping[str, Any]) -> None:
        if entry.get("cancelled"):
            # 用户取消后子进程会以非零码退出并上报 error，忽略这些收尾事件。
            return
        kind = event.get("event")
        if kind == "qr":
            entry.update({
                "status": "waiting",
                "image": event.get("data_url") or "",
                "image_path": event.get("image_path") or entry["image_path"],
                "message": "请使用智慧树 APP 扫码登录",
                "updated_at": time.time(),
            })
            self._log("二维码已生成，请使用智慧树 APP 扫码登录")
        elif kind == "scanned":
            entry.update({"status": "scanned", "message": "已扫码，请在手机上确认登录", "updated_at": time.time()})
            self._log("已扫码，等待手机端确认…")
        elif kind == "ok":
            entry.update({"status": "success", "message": "登录成功，Cookie 已保存", "updated_at": time.time()})
            self._session_cache.pop(account, None)
            thread = threading.Thread(
                target=self._verify_after_login,
                args=(account,),
                name=f"zhs-verify-{account}",
                daemon=True,
            )
            thread.start()
        elif kind == "error":
            entry.update({"status": "error", "message": str(event.get("message") or "扫码登录失败"), "updated_at": time.time()})

    def _verify_after_login(self, account: int) -> None:
        """扫码成功后立刻做一次服务端校验（后台线程），让界面显示真实登录态。"""
        if self._closing:
            return
        try:
            state = self.session_state(account, verify=True, force=True)
        except Exception as exc:  # noqa: BLE001 - 校验失败不影响登录结果
            self._log(f"ZHS 登录态校验失败：{exc}")
            return
        if state.get("status") == "ok":
            self._log("ZHS 登录态校验通过")
        else:
            self._log(f"ZHS 登录态校验未通过：{state.get('message') or state.get('status')}")

    def _terminate(self, process: Any) -> None:
        if process is None:
            return
        try:
            if process.poll() is None:
                terminator = getattr(self.launcher, "_terminate_process_tree", None)
                if callable(terminator):
                    terminator(process, "ZHS")
                else:
                    process.terminate()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------
    def session_state(
        self,
        account_id: Any = ACCOUNT_ID_DEFAULT,
        *,
        verify: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """返回登录态。

        verify=False：只看本地 cookies.json（若之前校验过则带缓存结论），不发网络请求，供 UI 轮询使用。
        verify=True：用 zhs 的 verify_session 向服务端确认会话是否仍然有效（结果按 cookies.json 的
        mtime 缓存，force=True 强制重新校验），用于「刷新登录态」「拉取课程」「开始刷课」等显式动作。
        """
        account = self.normalize_account_id(account_id)
        path = self.cookies_path(account)
        try:
            stamp = os.stat(path).st_mtime
        except OSError:
            self._session_cache.pop(account, None)
            return {
                "logged_in": False,
                "verified": False,
                "status": "none",
                "cookie_count": 0,
                "updated_at": 0.0,
                "checked_at": 0.0,
                "message": "尚未登录，请先扫码",
                "path": path,
            }

        base = self._cookies_file_state(path, stamp)
        if base.get("status") in ("corrupt", "expired"):
            return base
        if verify:
            result = self._verified_session(account, stamp, force=force)
            if result is not None:
                return {**base, **result, "path": path, "updated_at": stamp}
        cached = self._session_cache.get(account)
        if cached and cached.get("stamp") == stamp:
            return {**base, **cached["result"], "path": path, "updated_at": stamp}
        return base

    def _cookies_file_state(self, path: str, stamp: float) -> dict[str, Any]:
        """只看 cookies.json 的本地状态；logged_in 仅表示“存在登录凭证”，verified 才是服务端结论。"""
        state: dict[str, Any] = {
            "logged_in": False,
            "verified": False,
            "status": "expired",
            "cookie_count": 0,
            "updated_at": stamp,
            "checked_at": 0.0,
            "message": "未找到登录凭证，请重新扫码登录",
            "path": path,
        }
        try:
            with open(path, "r", encoding="utf-8") as handle:
                cookies = json.load(handle)
        except (OSError, json.JSONDecodeError):
            state["status"] = "corrupt"
            state["message"] = "Cookie 文件损坏，请重新登录"
            return state
        if not isinstance(cookies, list) or not cookies:
            state["status"] = "corrupt"
            state["message"] = "Cookie 为空，请重新登录"
            return state
        state["cookie_count"] = len(cookies)
        names = {str(item.get("name") or "") for item in cookies if isinstance(item, Mapping)}
        if names & {"CASTGC", "SESSION", "SERVERID"}:
            state["logged_in"] = True
            state["status"] = "unverified"
            state["message"] = "已保存登录凭证（点「刷新登录态」可校验是否仍然有效）"
        return state

    def _verified_session(self, account: int, stamp: float, *, force: bool) -> dict[str, Any] | None:
        cached = self._session_cache.get(account)
        if cached and cached.get("stamp") == stamp and not force:
            status = str((cached.get("result") or {}).get("status") or "")
            ttl = SESSION_CACHE_SECONDS_OK if status == "ok" else SESSION_CACHE_SECONDS_SHORT
            if time.time() - float(cached.get("checked_at") or 0.0) < ttl:
                return dict(cached["result"])
        if not self._session_lock.acquire(blocking=False):
            # 已有校验在跑：给出上次结论，避免 UI 卡在这里
            if cached and cached.get("stamp") == stamp:
                return dict(cached["result"])
            return {
                "logged_in": False,
                "verified": False,
                "status": "checking",
                "checked_at": 0.0,
                "message": "正在校验登录状态…",
            }
        try:
            checked_at = time.time()
            payload = self._run_session_check(account)
            status = str(payload.get("status") or "error")
            mapping = {
                "ok": (True, "登录状态有效"),
                "expired": (False, "登录凭证已过期，请重新扫码登录"),
                "unavailable": (False, "暂时无法校验登录状态，请检查网络后重试"),
                "none": (False, "尚未登录，请先扫码"),
                "error": (False, "登录状态校验失败：" + str(payload.get("message") or "")),
            }
            logged_in, message = mapping.get(status, (False, "登录状态未知"))
            result = {
                "logged_in": logged_in,
                "verified": status in ("ok", "expired"),
                "status": status,
                "cookie_count": int(payload.get("cookie_count") or 0),
                "checked_at": checked_at,
                "message": message,
                "uuid": payload.get("uuid") or "",
            }
            # 校验成功时桥接脚本会回写 cookies.json（补种 SESSION），mtime 会变，缓存键要跟着更新
            try:
                stamp = os.stat(self.cookies_path(account)).st_mtime
            except OSError:
                stamp = 0.0
            self._session_cache[account] = {"stamp": stamp, "result": result, "checked_at": checked_at}
            return result
        finally:
            self._session_lock.release()

    def _run_session_check(self, account: int) -> dict[str, Any]:
        python = self.venv_python()
        script = os.path.join(self.base_dir(), "src", "zhs_bridge", "session_check.py")
        if not os.path.isfile(python) or not os.path.isfile(script):
            return {"status": "unavailable", "message": "ZHS 运行环境不可用"}
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [python, script],
                cwd=self.account_dir(account),
                env=env,
                timeout=SESSION_CHECK_TIMEOUT_SECONDS,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            return {"status": "unavailable", "message": "登录状态校验超时"}
        except Exception as exc:  # noqa: BLE001 - 校验失败不能打断 UI
            return {"status": "error", "message": str(exc)}
        stdout = self._decode(completed.stdout or b"")
        stderr = self._decode(completed.stderr or b"")
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, Mapping):
                return dict(payload)
        detail = ""
        for line in reversed(stderr.splitlines()):
            if line.strip():
                detail = line.strip()[-300:]
                break
        return {"status": "error", "message": detail or ("返回码 " + str(completed.returncode))}

    def schedule_session_check(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> None:
        """后台校验一次登录态（不阻塞调用方）。

        面板每次轮询都读缓存，首次打开时凭证还没校验过，先返回“待校验”，
        由这里在后台补一次服务端确认，界面下一次轮询就能看到真实结果。
        """
        account = self.normalize_account_id(account_id)
        cached = self._session_cache.get(account)
        try:
            stamp = os.stat(self.cookies_path(account)).st_mtime
        except OSError:
            return
        if cached and cached.get("stamp") == stamp and cached.get("result"):
            return
        if account in self._session_pending:
            return
        self._session_pending.add(account)

        def run() -> None:
            try:
                self.session_state(account, verify=True)
            except Exception:  # noqa: BLE001 - 后台校验失败由下一次刷新处理
                pass
            finally:
                self._session_pending.discard(account)

        threading.Thread(target=run, name=f"zhs-session-{account}", daemon=True).start()

    def clear_credentials(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        """删除本地登录凭证（cookies.json）并清掉校验缓存。"""
        account = self.normalize_account_id(account_id)
        path = self.cookies_path(account)
        removed = False
        try:
            os.remove(path)
            removed = True
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._log(f"删除 ZHS 登录凭证失败：{exc}")
        self._session_cache.pop(account, None)
        entry = self._login_entry(account)
        if entry["status"] in ("success", "waiting", "scanned", "starting"):
            entry.update({
                "status": "idle",
                "message": "已清除登录凭证",
                "image": "",
                "updated_at": time.time(),
            })
        if removed:
            self._log("已清除 ZHS 登录凭证")
        return {
            "ok": True,
            "message": "已清除登录凭证" if removed else "没有可清除的登录凭证",
            "removed": removed,
            "session": self.session_state(account),
        }

    # ------------------------------------------------------------------
    # 课程列表
    # ------------------------------------------------------------------
    def fetch_courses(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        """运行 zhs fetch 拉课程，返回归一化课程列表。"""
        account = self.normalize_account_id(account_id)
        session = self.session_state(account, verify=True)
        if not session.get("logged_in"):
            return {"ok": False, "message": session.get("message") or "请先扫码登录", "courses": [], "fetched_at": 0.0}
        env = self.environment_state()
        if not env.get("ready"):
            return {"ok": False, "message": env.get("message") or "ZHS 运行环境不可用", "courses": [], "fetched_at": 0.0}
        self.ensure_account_dirs(account)
        with self._fetch_lock:
            command = [self.venv_python(), "-m", "zhs", "fetch", "--type", "all"]
            result = self._run_command(command, cwd=self.account_dir(account), timeout=FETCH_TIMEOUT_SECONDS)
            if result["code"] != 0:
                detail = ""
                for line in reversed((result["stderr"] or result["stdout"] or "").splitlines()):
                    if line.strip():
                        detail = line.strip()[:300]
                        break
                return {
                    "ok": False,
                    "message": "拉取课程失败：" + (detail or ("返回码 " + str(result["code"]))),
                    "courses": [],
                    "fetched_at": 0.0,
                }
        courses = self._read_execution(account)
        self._log(f"ZHS 课程拉取完成，共 {len(courses)} 门课程")
        return {"ok": True, "message": f"已获取 {len(courses)} 门课程", "courses": courses, "fetched_at": time.time()}

    def cached_courses(self, account_id: Any = ACCOUNT_ID_DEFAULT) -> dict[str, Any]:
        account = self.normalize_account_id(account_id)
        try:
            fetched_at = os.stat(self.execution_path(account)).st_mtime
        except OSError:
            return {"ok": False, "message": "", "courses": [], "fetched_at": 0.0}
        courses = self._read_execution(account)
        return {"ok": True, "message": "", "courses": courses, "fetched_at": fetched_at}

    def _read_execution(self, account: int) -> list[dict[str, Any]]:
        path = self.execution_path(account)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, Mapping):
            return []
        return normalize_execution(data)

    # ------------------------------------------------------------------
    # 运行命令构造
    # ------------------------------------------------------------------
    def build_command(
        self,
        action: str,
        courses: Sequence[Mapping[str, Any] | str] | None,
        settings: Mapping[str, Any] | None = None,
        *,
        submit_exam: bool = False,
        url: str | None = None,
    ) -> list[str]:
        """构造 zhs 子命令（play/homework/exam），不包含解释器。

        课程选择既接受 UI 归一化出来的课程对象（type/course_id/class_id），
        也接受 "type:courseId:classId" 形式的 key 字符串（前端历史格式的兜底）。
        多门课程会拼成重复的 -c 参数，由 zhs 在同一个进程里按给定顺序**串行**处理，
        不会并发，也不会额外起进程。
        """
        python_script = ["-m", "zhs"]
        action = (action or "play").strip().lower()
        if action not in ("play", "homework", "exam"):
            raise ValueError(f"不支持的 ZHS 动作：{action}")
        command = list(python_script) + [action]

        # 验证码救援：拿到课程页 URL（含 recruitAndCourseId）时直接用 --url 重跑那一门，
        # 不需要先反查数字 courseId。
        if url:
            target = str(url).strip()
            if action != "play":
                raise ValueError("按课程链接重跑只支持刷课（play）")
            if not target.startswith(("https://", "http://")):
                raise ValueError(f"课程链接格式不正确：{target}")
            return command + ["--url", target]

        normalized: list[dict[str, str]] = []
        invalid = 0
        for raw in courses or []:
            entry = _coerce_course_entry(raw)
            if entry is None:
                invalid += 1
                continue
            normalized.append(entry)
        if invalid:
            self._log(f"ZHS 课程选择中有 {invalid} 项无法识别，已忽略")

        if len(normalized) == 1:
            item = normalized[0]
            course_id = item["course_id"]
            class_id = item["class_id"]
            course_type = item["type"]
            if course_type == "ai" and course_id and class_id:
                command += ["--ai-course", course_id, "--ai-class", class_id]
            elif course_id:
                command += ["-c", course_id]
                if course_type in ("zhidao", "hike", "ai"):
                    command += ["--type", course_type]
            if action == "exam" and submit_exam:
                command.append("--submit")
            return command

        if normalized:
            entries: list[tuple[str, str]] = []
            for item in normalized:
                arg = _course_cli_arg(item)
                if not arg:
                    self._log(
                        f"ZHS 课程 {item['course_id']} 缺少 classId（AI 课程），已跳过"
                    )
                    continue
                entries.append((item["type"], arg))
            types = {course_type for course_type, _ in entries}
            if entries and action == "exam" and len(types) > 1:
                raise ValueError(
                    "考试模式一次只能处理同一类型的课程（知到或 AI），请分开选择后再运行"
                )
            if entries and action == "exam" and types == {"ai"} and len(entries) > 1:
                raise ValueError("考试模式一次只能选择一门 AI 课程，请取消多选后再运行")
            if entries:
                # 类型一致时显式给 --type：zhs 就不会为每门课再探测一次类型（省网络请求）
                if len(types) == 1:
                    command += ["--type", types.pop()]
                for _, arg in entries:
                    command += ["-c", arg]
                if action == "exam" and submit_exam:
                    command.append("--submit")
                return command

        # 未选择课程（或全部无法表达）：交给 zhs 自己遍历全部课程
        command += ["--type", "ai"] if action == "exam" else ["--type", "auto"]
        return command

    def build_play_command(self, courses=None, settings=None) -> list[str]:
        return self.build_command("play", courses, settings)

    # ------------------------------------------------------------------
    # 杂项
    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        logger = getattr(self.launcher, "log", None)
        if callable(logger):
            try:
                logger("zhs", message)
            except Exception:
                pass


def _parse_course_key(text: Any) -> dict[str, str] | None:
    """把 "type:courseId:classId" 形式的课程 key 解析成课程对象。"""
    parts = _to_text(text).split(":")
    if len(parts) < 2:
        return None
    course_type = parts[0].strip().lower()
    if course_type not in ("zhidao", "hike", "ai"):
        return None
    course_id = parts[1].strip()
    if not course_id:
        return None
    class_id = parts[2].strip() if len(parts) > 2 else ""
    return {"type": course_type, "course_id": course_id, "class_id": class_id}


def _coerce_course_entry(item: Any) -> dict[str, str] | None:
    """把 UI 传来的课程对象/课程 key 统一成 {type, course_id, class_id}。"""
    if isinstance(item, str):
        return _parse_course_key(item)
    if isinstance(item, Mapping):
        course_id = _to_text(item.get("course_id")).strip()
        if not course_id:
            return _parse_course_key(item.get("key"))
        course_type = _to_text(item.get("type")).strip().lower()
        if course_type not in ("zhidao", "hike", "ai"):
            course_type = ""
        return {
            "type": course_type,
            "course_id": course_id,
            "class_id": _to_text(item.get("class_id")).strip(),
        }
    return None


def _course_cli_arg(entry: Mapping[str, str]) -> str:
    """把课程对象转成 zhs -c 能接受的形式：知到/Hike 用 courseId，AI 用 courseId:classId。"""
    course_id = _to_text(entry.get("course_id")).strip()
    if not course_id:
        return ""
    if entry.get("type") == "ai":
        class_id = _to_text(entry.get("class_id")).strip()
        return course_id + ":" + class_id if class_id else ""
    return course_id


def normalize_execution(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 .zhs/execution.json 归一化为 UI 友好的课程列表。"""
    type_labels = {"zhidao": "智慧树知到", "hike": "Hike 课程", "ai": "AI 课程"}
    courses: list[dict[str, Any]] = []
    for course_type in ("zhidao", "hike", "ai"):
        entries = data.get(course_type) or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            course_id = str(entry.get("courseId") or "")
            if not course_id:
                continue
            class_id = str(entry.get("classId") or "")
            name = _to_text(entry.get("name"), course_id)
            courses.append({
                "type": course_type,
                "type_label": type_labels.get(course_type, course_type),
                "name": name,
                "course_id": course_id,
                "class_id": class_id,
                "key": f"{course_type}:{course_id}:{class_id}",
                "label": f"[{type_labels.get(course_type, course_type)}] {name}",
            })
    return courses

# ----------------------------------------------------------------------
# 验证码中断识别（zhs 的 stdout / 日志）
# ----------------------------------------------------------------------
# zhs 在知到视频心跳返回 -12 时会打：
#   Course stopped: captcha required (racId=<racId>, course=<课程名>, videoId=<数字>)
# 这条日志同时带齐了「哪门课」和「这门课的课程页 URL」（racId 就是 recruitAndCourseId），
# 因此启动器不需要额外请求就能给用户一个「打开课程页验证」的入口。
CAPTCHA_LOG_RE = re.compile(
    r"Course stopped:\s*captcha required\s*\(racId=(?P<rac_id>[^,)]+),\s*course=(?P<course>[^,)]+),\s*videoId=(?P<video_id>\d+)\)"
)
STUDY_PAGE_URL = "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={rac_id}"


def parse_captcha_line(text: Any) -> dict[str, Any] | None:
    """从 zhs 日志行识别「验证码中断」，返回课程信息；不是这类行时返回 None。"""
    match = CAPTCHA_LOG_RE.search(str(text or ""))
    if not match:
        return None
    rac_id = match.group("rac_id").strip()
    course = match.group("course").strip()
    video_id = match.group("video_id").strip()
    return {
        "course": course,
        "racId": rac_id,
        "videoId": video_id,
        "url": course_page_url(rac_id),
        "message": f"《{course}》遇到智慧树验证码，这门课已停止",
    }


def course_page_url(rac_id: Any) -> str:
    """用 recruitAndCourseId 拼出知到课程学习页 URL。"""
    return STUDY_PAGE_URL.format(rac_id=str(rac_id or "").strip())
