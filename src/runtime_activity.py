"""Derive compact Web UI activity state from Autovisor console logs."""

from __future__ import annotations

import re
from collections.abc import Iterable


_TIMESTAMP_RE = re.compile(r"^\s*\[\d{1,2}:\d{2}:\d{2}\]\s*")
_LEVEL_RE = re.compile(r"^\s*\[(?:INFO|WARN|ERROR|DEBUG|FAIL|OK)\]\s*", re.I)
_COURSE_POSITION_RE = re.compile(
    r"开始处理第\s*(?P<index>\d+)\s*/\s*(?P<total>\d+)\s*门课程"
)
_COURSE_TITLE_RE = re.compile(r"当前课程\s*[:：]\s*<<(?P<title>.+?)>>")
_PROGRESS_RE = re.compile(
    r"(?:完成进度|学习进度|播放进度)\s*[:：]?\s*(?P<percent>\d{1,3})%"
)
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.I)

_START_MARKERS = ("正在启动任务", "程序启动中")
_LOGIN_MARKERS = (
    "正在等待登录完成",
    "登录信息已提交",
    "请手动填写账号密码",
)
_TEST_MARKERS = (
    "开始处理测验",
    "开始答题",
    "提交试卷",
    "作业提交完成",
)
_FAILURE_MARKERS = (
    "执行失败",
    "系统出错",
    "登录状态已失效",
    "任务已中断",
    "登录未完成",
    "浏览器启动失败",
    "配置文件错误",
    "依赖文件缺失",
    "编码错误",
    "课程队列部分失败",
)


def _clean_line(value: object) -> str:
    text = _TIMESTAMP_RE.sub("", str(value).strip())
    return text.replace("\x00", "")


def _display_message(line: str, limit: int = 160) -> str:
    message = _LEVEL_RE.sub("", line).strip()
    message = _URL_QUERY_RE.sub(r"\1", message)
    message = " ".join(message.split())
    if len(message) > limit:
        return message[: limit - 1].rstrip() + "…"
    return message


def summarize_autovisor_activity(
    lines: Iterable[object] | None,
    *,
    running: bool = False,
    starting: bool = False,
) -> dict[str, object]:
    """Return stable, JSON-friendly status derived from the latest log window."""

    state: dict[str, object] = {
        "phase": "idle",
        "label": "等待启动",
        "progress_percent": None,
        "course": None,
        "course_index": None,
        "course_total": None,
        "last_error": None,
    }

    for raw_line in lines or ():
        line = _clean_line(raw_line)
        if not line:
            continue

        if any(marker in line for marker in _START_MARKERS):
            state.update(
                phase="starting",
                label="正在启动",
                progress_percent=None,
                course=None,
                course_index=None,
                course_total=None,
                last_error=None,
            )
            continue

        position = _COURSE_POSITION_RE.search(line)
        if position:
            state.update(
                phase="course",
                label="正在准备课程",
                progress_percent=None,
                course=None,
                course_index=int(position.group("index")),
                course_total=int(position.group("total")),
            )
            continue

        title = _COURSE_TITLE_RE.search(line)
        if title:
            state.update(
                phase="course",
                label="正在学习课程",
                course=_display_message(title.group("title"), limit=80),
            )
            continue

        if any(marker in line for marker in _LOGIN_MARKERS):
            state.update(phase="login", label="正在登录", progress_percent=None)
            continue

        if any(marker in line for marker in _TEST_MARKERS):
            label = "测验已提交" if "作业提交完成" in line else "正在处理测验"
            state.update(phase="test", label=label)
            continue

        progress = _PROGRESS_RE.search(line)
        if progress:
            percent = max(0, min(100, int(progress.group("percent"))))
            state.update(
                phase="video",
                label="正在播放课程",
                progress_percent=percent,
            )
            continue

        if "所有课程已学习完毕" in line:
            state.update(
                phase="completed",
                label="全部课程已完成",
                progress_percent=100,
                last_error=None,
            )
            continue

        if "执行完成" in line and "门课程" in line:
            state.update(
                phase="course",
                label="当前课程已完成",
                progress_percent=100,
            )
            continue

        is_failure = "[ERROR]" in line.upper() or any(
            marker in line for marker in _FAILURE_MARKERS
        )
        if is_failure:
            state.update(
                phase="failed",
                label="运行失败",
                last_error=_display_message(line),
            )

    active = bool(running or starting)
    if starting and state["phase"] in {"idle", "completed", "failed", "stopped"}:
        state.update(phase="starting", label="正在启动", progress_percent=None)
    elif running and state["phase"] == "idle":
        state.update(phase="starting", label="正在启动")
    elif not active and state["phase"] in {
        "starting",
        "login",
        "course",
        "video",
        "test",
    }:
        state.update(phase="stopped", label="任务已停止")

    return state
