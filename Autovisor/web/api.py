# encoding=utf-8
"""
Autovisor Web Dashboard API
Cal.com-style dashboard backend with FastAPI
"""

import asyncio
import json
import os
import re
import time
import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 入口在 Autovisor 目录运行，modules 是同级包。不要永久修改 sys.path，
# 否则同名 Autovisor.py 会遮蔽仓库中的 Autovisor 命名空间。
from modules.configs import Config
from modules.logger import Logger
from web.task_supervisor import (
    TaskAlreadyRunning,
    TaskSnapshot,
    TaskSupervisor,
)

# ============================================================
# Configuration
# ============================================================
CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs.ini"
COOKIES_PATH = Path(__file__).resolve().parent.parent / "res" / "cookies.json"
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

# ============================================================
# Pydantic Models
# ============================================================

class ApiResponse(BaseModel):
    success: bool = True
    data: Optional[Any] = None
    message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class ConfigUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    driver: Optional[str] = None
    exe_path: Optional[str] = None
    enable_auto_captcha: Optional[bool] = None
    enable_hide_window: Optional[bool] = None
    limit_max_time: Optional[float] = None
    limit_speed: Optional[float] = None
    sound_off: Optional[bool] = None
    course_urls: Optional[list[str]] = None

class TaskStartRequest(BaseModel):
    course_url: Optional[str] = Field(default=None, max_length=2048)
    mode: Literal["single", "multi"] = "single"

# ============================================================
# State Management
# ============================================================

@dataclass
class AppState:
    config: Optional[Config] = None
    is_running: bool = False
    current_task: Optional[str] = None
    task_mode: Optional[str] = None
    task_pid: Optional[int] = None
    task_start_time: Optional[float] = None
    last_exit_code: Optional[int] = None
    logs: list[dict] = field(default_factory=list)
    max_logs: int = 1000
    stats: dict = field(default_factory=lambda: {
        "total_sessions": 0,
        "total_videos": 0,
        "total_tests": 0,
        "success_rate": None,
    })
    courses: list[dict] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)

app_state = AppState()

# ============================================================
# Logger Bridge (captures logs for dashboard)
# ============================================================

class DashboardLogger:
    def __init__(self, max_logs: int = 1000):
        self.logs: list[dict] = []
        self.max_logs = max_logs

    def info(self, msg: str, shift: bool = False):
        entry = {
            "level": "INFO",
            "message": msg,
            "timestamp": datetime.now().isoformat(),
            "shift": shift,
        }
        self._append(entry)

    def warn(self, msg: str, shift: bool = False):
        entry = {
            "level": "WARN",
            "message": msg,
            "timestamp": datetime.now().isoformat(),
            "shift": shift,
        }
        self._append(entry)

    def error(self, msg: str, shift: bool = False):
        entry = {
            "level": "ERROR",
            "message": msg,
            "timestamp": datetime.now().isoformat(),
            "shift": shift,
        }
        self._append(entry)

    def _append(self, entry: dict):
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]
        # Also write to file logger if available
        try:
            logger = Logger()
            if entry["level"] == "INFO":
                logger.info(entry["message"], shift=entry.get("shift", False))
            elif entry["level"] == "WARN":
                logger.warn(entry["message"], shift=entry.get("shift", False))
            elif entry["level"] == "ERROR":
                logger.error(entry["message"], shift=entry.get("shift", False))
        except Exception:
            pass

dashboard_logger = DashboardLogger()


def _classify_task_log_line(message: str) -> str:
    upper = message.upper()
    if (
        "ERROR" in upper
        or "TRACEBACK" in upper
        or "EXCEPTION" in upper
        or "错误" in message
        or "失败" in message
    ):
        return "ERROR"
    if "WARN" in upper or "WARNING" in upper or "警告" in message:
        return "WARN"
    return "TASK"


def _read_task_log_entries(log_path: Path, max_lines: int) -> list[dict]:
    if max_lines <= 0 or not log_path.is_file():
        return []
    max_bytes = 256 * 1024
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            chunk = handle.read()
        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if start and lines:
            lines = lines[1:]
        lines = [line for line in lines if line.strip()][-max_lines:]
        timestamp = datetime.fromtimestamp(log_path.stat().st_mtime).isoformat()
    except OSError as exc:
        dashboard_logger.warn(f"Read task log warning: {exc}")
        return []

    return [
        {
            "level": _classify_task_log_line(line),
            "message": line,
            "timestamp": timestamp,
            "shift": False,
            "source": "task",
        }
        for line in lines
    ]


def _get_dashboard_logs(limit: int, level: Optional[str] = None) -> tuple[list[dict], int]:
    bounded_limit = max(0, min(limit, 1000))
    task_log_path = getattr(task_supervisor, "log_path", None)
    task_scan_limit = min(max(bounded_limit * 4, 200), 1000) if bounded_limit else 0
    logs = list(dashboard_logger.logs)
    if task_log_path:
        logs.extend(_read_task_log_entries(Path(task_log_path), task_scan_limit))
    if level:
        target = level.upper()
        logs = [log for log in logs if log.get("level") == target]
    logs.sort(key=lambda log: log.get("timestamp") or "")
    total = len(logs)
    return logs[-bounded_limit:] if bounded_limit else [], total


_PROGRESS_COMPLETE_RE = re.compile(
    r"(?:完成进度|学习进度|播放进度)\s*[:：]?\s*(?:\|.*?\|\s*)?100%"
)
_VIDEO_COMPLETION_MARKERS = (
    "[OK] 视频播放完成",
    "所有视频已完成",
    "视频已完成",
    "已完成！",
)
_TEST_COMPLETION_MARKERS = (
    "作业提交完成",
    "测验完成",
    "测试已完成",
    "答题并提交成功",
)
_SESSION_START_MARKERS = (
    "程序启动中",
    "Task started:",
)
_SESSION_SUCCESS_MARKERS = (
    "所有课程已学习完毕",
    "所有课程已完成",
    "课程队列结束: 成功",
)
_SESSION_FAILURE_MARKERS = (
    "[ERROR]",
    "[FAIL]",
    "Traceback",
    "执行失败",
    "任务已中断",
    "返回码:",
)
_COURSE_POSITION_RE = re.compile(
    r"开始处理第\s*(?P<index>\d+)\s*/\s*(?P<total>\d+)\s*门课程"
)
_COURSE_TITLE_RE = re.compile(r"当前课程\s*[:：]\s*<<(?P<title>.+?)>>")
_COURSE_PROGRESS_RE = re.compile(
    r"(?:完成进度|学习进度|播放进度)\s*[:：]?\s*(?:\|.*?\|\s*)?"
    r"(?P<percent>\d{1,3})%"
)
_COURSE_TERMINAL_RE = re.compile(
    r"第\s*(?P<index>\d+)\s*/\s*(?P<total>\d+)\s*门课程.*?"
    r"(?P<result>执行完成|执行失败)"
)


def _unique_marker_matches(lines: list[str], markers: tuple[str, ...]) -> set[str]:
    return {line.strip() for line in lines if any(marker in line for marker in markers)}


def _derive_dashboard_stats() -> dict:
    logs, _total = _get_dashboard_logs(1000)
    lines = [str(log.get("message", "")) for log in logs]
    video_marker_evidence = _unique_marker_matches(lines, _VIDEO_COMPLETION_MARKERS)
    video_progress_evidence = {
        line.strip() for line in lines if _PROGRESS_COMPLETE_RE.search(line)
    }
    test_evidence = _unique_marker_matches(lines, _TEST_COMPLETION_MARKERS)
    success_evidence = _unique_marker_matches(lines, _SESSION_SUCCESS_MARKERS)
    failure_evidence = _unique_marker_matches(lines, _SESSION_FAILURE_MARKERS)

    total_sessions = max(
        int(app_state.stats.get("total_sessions") or 0),
        len(_unique_marker_matches(lines, _SESSION_START_MARKERS)),
    )
    total_videos = max(
        int(app_state.stats.get("total_videos") or 0),
        len(video_marker_evidence),
        len(video_progress_evidence),
    )
    total_tests = max(
        int(app_state.stats.get("total_tests") or 0),
        len(test_evidence),
    )

    success_count = len(success_evidence)
    failure_count = len(failure_evidence)
    success_rate = None
    if success_count or failure_count:
        success_rate = success_count / (success_count + failure_count)

    return {
        "total_sessions": total_sessions,
        "total_videos": total_videos,
        "total_tests": total_tests,
        "success_rate": success_rate,
        "stats_source": "runtime_logs",
    }


def _derive_course_rows(urls: list[str]) -> list[dict]:
    logs, _total = _get_dashboard_logs(1000)
    lines = [str(log.get("message", "")) for log in logs]
    current_index: int | None = None
    titles: dict[int, str] = {}
    progress: dict[int, int] = {}
    completed: set[int] = set()
    failed: set[int] = set()
    all_completed = False

    for line in lines:
        position = _COURSE_POSITION_RE.search(line)
        if position:
            current_index = int(position.group("index"))

        title = _COURSE_TITLE_RE.search(line)
        if title and current_index:
            titles[current_index] = title.group("title").strip()

        progress_match = _COURSE_PROGRESS_RE.search(line)
        if progress_match and current_index:
            percent = max(0, min(100, int(progress_match.group("percent"))))
            progress[current_index] = max(progress.get(current_index, 0), percent)

        terminal = _COURSE_TERMINAL_RE.search(line)
        if terminal:
            index = int(terminal.group("index"))
            if terminal.group("result") == "执行完成":
                completed.add(index)
                progress[index] = 100
            else:
                failed.add(index)

        if any(marker in line for marker in ("所有课程已学习完毕", "所有课程已完成")):
            all_completed = True

    rows = []
    for index, url in enumerate(urls, 1):
        row_progress = progress.get(index, 0)
        status = "pending"
        if all_completed or index in completed:
            status = "completed"
            row_progress = 100
        elif index in failed:
            status = "failed"
        elif current_index == index:
            status = "running"

        rows.append(
            {
                "id": index,
                "url": url,
                "name": titles.get(index) or f"课程 {index}",
                "progress": row_progress,
                "status": status,
            }
        )
    return rows

# ============================================================
# Config Manager
# ============================================================

class ConfigManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._config: Optional[Config] = None
        self._last_read: float = 0

    def get(self) -> Config:
        now = time.time()
        if self._config is None or now - self._last_read > 2:
            if self.config_path.exists():
                self._config = Config(str(self.config_path))
            else:
                # Return default config with minimal safe defaults
                self._config = Config()
                self._config.username = ""
                self._config.password = ""
                self._config.driver = "edge"
                self._config.exe_path = ""
                self._config.enableAutoCaptcha = True
                self._config.enableHideWindow = False
                self._config.limitMaxTime = 30.0
                self._config.limitSpeed = 1.5
                self._config.soundOff = True
                self._config.course_urls = []
            self._last_read = now
        return self._config

    def update(self, data: ConfigUpdate) -> dict:
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        if self.config_path.exists():
            parser.read(self.config_path, encoding="utf-8")

        # Ensure sections exist
        for section in ["user-account", "browser-option", "script-option", "course-option", "course-url"]:
            if not parser.has_section(section):
                parser.add_section(section)

        if data.username is not None:
            parser.set("user-account", "username", data.username)
        if data.password is not None:
            parser.set("user-account", "password", data.password)
        if data.driver is not None:
            parser.set("browser-option", "driver", data.driver)
        if data.exe_path is not None:
            parser.set("browser-option", "EXE_PATH", data.exe_path)
        if data.enable_auto_captcha is not None:
            parser.set("script-option", "enableAutoCaptcha", str(data.enable_auto_captcha))
        if data.enable_hide_window is not None:
            parser.set("script-option", "enableHideWindow", str(data.enable_hide_window))
        if data.limit_max_time is not None:
            parser.set("course-option", "limitMaxTime", str(data.limit_max_time))
        if data.limit_speed is not None:
            parser.set("course-option", "limitSpeed", str(data.limit_speed))
        if data.sound_off is not None:
            parser.set("course-option", "soundOff", str(data.sound_off))
        if data.course_urls is not None:
            # Clear existing URLs
            for key in list(parser.options("course-url")):
                parser.remove_option("course-url", key)
            for i, url in enumerate(data.course_urls, 1):
                parser.set("course-url", f"URL{i}", url)

        with open(self.config_path, "w", encoding="utf-8") as f:
            parser.write(f)

        self._last_read = 0  # Force refresh
        return {"updated": True, "path": str(self.config_path)}

    def to_dict(self) -> dict:
        cfg = self.get()
        result = {
            "username": getattr(cfg, "username", ""),
            "password": getattr(cfg, "password", ""),
            "driver": getattr(cfg, "driver", "edge"),
            "exe_path": getattr(cfg, "exe_path", ""),
            "enable_auto_captcha": getattr(cfg, "enableAutoCaptcha", True),
            "enable_hide_window": getattr(cfg, "enableHideWindow", False),
            "limit_max_time": getattr(cfg, "limitMaxTime", 30.0),
            "limit_speed": getattr(cfg, "limitSpeed", 1.5),
            "sound_off": getattr(cfg, "soundOff", True),
            "course_urls": getattr(cfg, "course_urls", []),
        }
        # Ensure exe_path key exists even if missing in config
        if not hasattr(cfg, "exe_path"):
            result["exe_path"] = ""
        return result

config_manager = ConfigManager(CONFIG_PATH)
task_supervisor = TaskSupervisor(
    CONFIG_PATH.parent,
    log_path=LOGS_DIR / "DashboardTask.log",
)


def _sync_task_state(snapshot: TaskSnapshot | None = None) -> TaskSnapshot:
    """用真实子进程状态覆盖 Dashboard 的兼容状态字段。"""
    snapshot = snapshot or task_supervisor.refresh()
    app_state.is_running = snapshot.is_running
    app_state.current_task = snapshot.task_id
    app_state.task_mode = snapshot.mode
    app_state.task_pid = snapshot.pid
    app_state.task_start_time = snapshot.start_time
    app_state.last_exit_code = snapshot.exit_code
    return snapshot

# ============================================================
# FastAPI App
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    dashboard_logger.info("Autovisor Web API started")
    try:
        cfg = config_manager.get()
        dashboard_logger.info(f"Config loaded: driver={cfg.driver}")
    except Exception as e:
        dashboard_logger.warn(f"Config load warning: {e}")
    try:
        yield
    finally:
        # Dashboard 退出不能遗留浏览器或多账号子进程。
        stopped, _snapshot = await asyncio.to_thread(task_supervisor.stop)
        if stopped:
            dashboard_logger.info("Running task stopped during API shutdown")
        _sync_task_state()
        dashboard_logger.info("Autovisor Web API shutting down")

app = FastAPI(
    title="Autovisor Dashboard API",
    description="Cal.com-style dashboard backend for Autovisor",
    version="3.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Global Exception Handler
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    dashboard_logger.error(f"Unhandled exception: {repr(exc)}")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="Internal Server Error",
            detail=str(exc),
        ).model_dump(),
    )

# ============================================================
# API Routes
# ============================================================

@app.get("/api/health", response_model=ApiResponse)
async def health_check():
    return ApiResponse(data={"status": "ok", "version": "3.1.0"})

@app.get("/api/config", response_model=ApiResponse)
async def get_config():
    try:
        data = config_manager.to_dict()
        # Mask password
        if data.get("password"):
            data["password"] = "*" * len(data["password"])
        return ApiResponse(data=data)
    except Exception as e:
        dashboard_logger.error(f"Get config failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config", response_model=ApiResponse)
async def update_config(payload: ConfigUpdate):
    try:
        result = config_manager.update(payload)
        dashboard_logger.info("Configuration updated via API")
        return ApiResponse(data=result, message="Configuration updated")
    except Exception as e:
        dashboard_logger.error(f"Update config failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs", response_model=ApiResponse)
async def get_logs(limit: int = 100, level: Optional[str] = None):
    logs, total = _get_dashboard_logs(limit, level)
    return ApiResponse(data={"logs": logs, "total": total})

@app.get("/api/stats", response_model=ApiResponse)
async def get_stats():
    return ApiResponse(data=_derive_dashboard_stats())

@app.get("/api/status", response_model=ApiResponse)
async def get_status():
    snapshot = _sync_task_state()
    uptime = 0.0
    if snapshot.start_time:
        uptime = max(0.0, time.time() - snapshot.start_time)
    return ApiResponse(data={
        "is_running": snapshot.is_running,
        "current_task": snapshot.task_id,
        "mode": snapshot.mode,
        "pid": snapshot.pid,
        "exit_code": snapshot.exit_code,
        "uptime_seconds": uptime,
        "total_sessions": app_state.stats["total_sessions"],
    })

@app.post("/api/tasks/start", response_model=ApiResponse)
async def start_task(request: TaskStartRequest):
    if request.course_url:
        parsed = urlsplit(request.course_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(status_code=422, detail="Invalid course URL")
    try:
        snapshot = await asyncio.to_thread(
            task_supervisor.launch,
            mode=request.mode,
            config_path=config_manager.config_path,
            course_url=request.course_url,
        )
    except TaskAlreadyRunning as exc:
        _sync_task_state()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        dashboard_logger.error(f"Task launch failed: {exc}")
        raise HTTPException(status_code=500, detail="Task launch failed") from exc

    _sync_task_state(snapshot)
    app_state.stats["total_sessions"] += 1
    dashboard_logger.info(
        f"Task started: {snapshot.task_id} "
        f"(mode={snapshot.mode}, pid={snapshot.pid})"
    )
    return ApiResponse(
        data={
            "task_id": snapshot.task_id,
            "mode": snapshot.mode,
            "pid": snapshot.pid,
        },
        message="Task started successfully",
    )

@app.post("/api/tasks/stop", response_model=ApiResponse)
async def stop_task():
    stopped, snapshot = await asyncio.to_thread(task_supervisor.stop)
    _sync_task_state(snapshot)
    if not stopped:
        return ApiResponse(data={}, message="No task running")
    dashboard_logger.info("Task stopped by user")
    return ApiResponse(
        data={"exit_code": snapshot.exit_code},
        message="Task stopped successfully",
    )

@app.get("/api/courses", response_model=ApiResponse)
async def get_courses():
    try:
        cfg = config_manager.get()
        urls = getattr(cfg, "course_urls", [])
        courses = _derive_course_rows(list(urls))
        return ApiResponse(data={"courses": courses})
    except Exception as e:
        dashboard_logger.error(f"Get courses failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/logs", response_model=ApiResponse)
async def list_log_files():
    try:
        if not LOGS_DIR.exists():
            return ApiResponse(data={"files": []})
        files = sorted(LOGS_DIR.glob("Log*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return ApiResponse(data={
            "files": [
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                }
                for f in files[:50]
            ]
        })
    except Exception as e:
        dashboard_logger.error(f"List logs failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/logs/{filename}", response_model=ApiResponse)
async def read_log_file(filename: str):
    try:
        file_path = LOGS_DIR / filename
        # Security: prevent directory traversal
        if not file_path.resolve().is_relative_to(LOGS_DIR.resolve()):
            raise HTTPException(status_code=403, detail="Access denied")
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return ApiResponse(data={"filename": filename, "content": content})
    except HTTPException:
        raise
    except Exception as e:
        dashboard_logger.error(f"Read log failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# Static Dashboard Route
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    dashboard_path = Path(__file__).resolve().parent / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Dashboard not found")

# ============================================================
# Main Entry
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
