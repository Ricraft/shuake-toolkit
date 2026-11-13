"""ZHS 控制台日志美化：给 zhs 的进度行补上课程名。

zhs 的进度行只有视频 ID（playing 262043 [...] 62.5%），多门课串行刷时看不出
当前是哪一门。这里按 zhs 自己打印的「新课程 / 课程 / 课时」记录上下文，
把进度行改写成「playing 课程名（262043） [...] 62.5%」。

只处理显示文本，不改 zhs 源码，也不影响验证码识别（进度行不会误伤 captcha 日志）。
"""

from __future__ import annotations

import re
from typing import Any


# 终端颜色控制符：AI 课程的课程名带 ANSI 高亮，日志里不该出现
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# [知到] 新课程: 3D打印技术与应用
COURSE_RE = re.compile(r"新课程[:：]\s*(?P<name>.+?)\s*$")
# [AI] 课程: xxx（AI 课程的标题行；不会匹配到「完成课程:」）
AI_COURSE_RE = re.compile(r"^(?:\[[^\]]+\]\s*)?课程[:：]\s*(?P<name>.+?)\s*$")
LESSON_RE = re.compile(r"课时[:：]\s*(?P<name>.+?)\s*$")
# 知到：playing 262043 [####   ] 62.5%
# AI：  playing [####   ] 62.5%
# 暂停/答题：pause / answering [####   ] 12.5%
PROGRESS_RE = re.compile(
    r"^(?P<action>playing|answering|pause)"
    r"(?:\s+(?P<video_id>\d+))?"
    r"\s+(?P<bar>\[.*)$"
)
DONE_RE = re.compile(r"(?:完成课程|课程完成)[:：]")


class ZhsLogFormatter:
    """有状态地把 zhs 日志行转换成更易读的形式。"""

    def __init__(self) -> None:
        self.course_name = ""
        self.lesson_name = ""

    def reset(self) -> None:
        self.course_name = ""
        self.lesson_name = ""

    def format(self, message: Any) -> str:
        text = ANSI_RE.sub("", str(message or ""))
        progress = PROGRESS_RE.match(text)
        if progress:
            return self._format_progress(progress)

        match = COURSE_RE.search(text)
        if match:
            self.course_name = match.group("name").strip()
            self.lesson_name = ""
            return text

        match = AI_COURSE_RE.match(text)
        if match:
            self.course_name = match.group("name").strip()
            self.lesson_name = ""
            return text

        match = LESSON_RE.search(text)
        if match:
            self.lesson_name = match.group("name").strip()
            return text

        if DONE_RE.search(text):
            self.lesson_name = ""
        return text

    def _format_progress(self, progress: re.Match[str]) -> str:
        action = progress.group("action")
        video_id = progress.group("video_id")
        bar = progress.group("bar")
        course = self.course_name
        if not course:
            return progress.group(0)
        if video_id:
            return f"{action} {course}（{video_id}） {bar}"
        return f"{action} {course} {bar}"