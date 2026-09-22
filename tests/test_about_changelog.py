# encoding=utf-8
"""关于页更新日志必须记录当前版本的最新改动。"""

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _page() -> str:
    for path in (PROJECT_ROOT / "web").glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "更新日志" in text:
            return text
    raise AssertionError("about page not found")


def _changelog() -> str:
    return _page().split("更新日志", 1)[1]


def test_changelog_lists_current_launcher_version_first():
    launcher = (PROJECT_ROOT / "统一启动器.py").read_text(encoding="utf-8")
    match = re.search(r'LAUNCHER_VERSION\s*=\s*"([^"]+)"', launcher)
    assert match, "LAUNCHER_VERSION not found"
    version = match.group(1)

    first = re.search(
        r"changelog-item[\s\S]*?font-semibold\">([^<]+)<",
        _changelog(),
    )
    assert first, "no changelog entry found"
    assert first.group(1).startswith(version), (first.group(1), version)


def test_changelog_documents_this_release():
    changelog = _changelog()
    for keyword in (
        "洛天依单课程刷课",
        "洛天依课程表",
        "工具调用与确认卡片",
        "拉取账号课程",
        "刷完自动关闭程序",
        "自定义人格提示词",
        "核心设置 AI 模型卡片对齐",
        "Yatori 章节任务开关不再被覆盖",
        "查找课程不再意外打开浏览器",
        "拉取课程结果不再无回执",
    ):
        assert keyword in changelog, keyword
