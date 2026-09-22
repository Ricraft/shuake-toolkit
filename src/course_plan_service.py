"""Persistent course registry and Chinese-friendly course name matching."""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from collections.abc import Callable
from pathlib import Path

from src.atomic_io import atomic_dump_json


SUPPORTED_CORES = ("yatori", "autovisor")
_NON_NAME_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def normalize_course_name(value) -> str:
    """折叠全半角、大小写、空格与标点：高数 A2 == 高数A2 == 高数ａ２。"""
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return _NON_NAME_RE.sub("", text)


def clean_name_list(value) -> list[str]:
    """把多行文本、逗号分隔文本或列表统一成去重的课程名列表。"""
    if isinstance(value, str):
        items = (
            value.replace(",", "\n")
            .replace("，", "\n")
            .splitlines()
        )
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    result: list[str] = []
    for item in items:
        name = str(item or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def normalize_entry(
    raw,
    *,
    index: int = 0,
    core: str | None = None,
    account_index: int | None = None,
) -> dict | None:
    """Return a validated registry entry, or ``None`` when it is unusable."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    entry_core = str(raw.get("core") or core or "").strip().lower()
    if entry_core not in SUPPORTED_CORES:
        return None
    account = raw.get("accountIndex", account_index if account_index is not None else 0)
    try:
        account = int(account)
    except (TypeError, ValueError):
        account = 0
    return {
        "id": str(raw.get("id") or f"{entry_core}-{index}").strip(),
        "name": name,
        "aliases": clean_name_list(raw.get("aliases")),
        "core": entry_core,
        "accountIndex": max(account, 0),
        "courseUrl": str(raw.get("courseUrl") or "").strip(),
        "skipQuestions": bool(raw.get("skipQuestions", False)),
        "maxMinutes": str(raw.get("maxMinutes") or "").strip(),
    }


class CoursePlanService:
    """Read, match and persist the user's pre-filled course lists."""

    def __init__(
        self,
        path: str | Path,
        *,
        logger: Callable[[str], None] | None = None,
    ):
        self.path = Path(path)
        self._logger = logger or (lambda _message: None)
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict:
        return {"version": 1, "courses": []}

    def load(self) -> dict:
        with self._lock:
            if not self.path.exists():
                return self._empty()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._logger(f"[课程表] 读取失败，已忽略损坏文件: {exc}")
                return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        courses = []
        for index, raw in enumerate(data.get("courses") or []):
            entry = normalize_entry(raw, index=index)
            if entry:
                courses.append(entry)
        return {"version": 1, "courses": courses}

    def save(self, payload) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "message": "课程表格式错误"}
        raw_courses = payload.get("courses")
        if raw_courses is None:
            raw_courses = []
        if not isinstance(raw_courses, list):
            return {"ok": False, "message": "课程表必须是列表"}
        courses = []
        for index, raw in enumerate(raw_courses):
            entry = normalize_entry(raw, index=index)
            if entry is None:
                return {
                    "ok": False,
                    "message": f"第 {index + 1} 门课程缺少课程名或核心类型",
                }
            courses.append(entry)
        with self._lock:
            try:
                atomic_dump_json(self.path, {"version": 1, "courses": courses})
            except Exception as exc:
                return {"ok": False, "message": f"课程表保存失败: {exc}"}
        return {"ok": True, "courses": courses}

    def list(self, *, core=None, account_index=None) -> list[dict]:
        courses = self.load()["courses"]
        result = []
        for entry in courses:
            if core and entry["core"] != core:
                continue
            if account_index is not None:
                try:
                    wanted = int(account_index)
                except (TypeError, ValueError):
                    continue
                if entry["accountIndex"] != wanted:
                    continue
            result.append(dict(entry))
        return result

    @staticmethod
    def score_entry(query, entry: dict) -> float:
        normalized_query = normalize_course_name(query)
        if not normalized_query or not isinstance(entry, dict):
            return 0.0
        best = 0.0
        candidates = [entry.get("name", ""), *(entry.get("aliases") or [])]
        for candidate in candidates:
            normalized = normalize_course_name(candidate)
            if not normalized:
                continue
            if normalized == normalized_query:
                best = max(best, 1.0)
            elif normalized_query in normalized or normalized in normalized_query:
                ratio = min(len(normalized), len(normalized_query)) / max(
                    len(normalized), len(normalized_query)
                )
                best = max(best, 0.5 + 0.4 * ratio)
        return best

    def match(self, query, entries) -> list[tuple[dict, float]]:
        scored = []
        for entry in entries or []:
            score = self.score_entry(query, entry)
            if score > 0:
                scored.append((entry, score))
        scored.sort(
            key=lambda item: (
                -item[1],
                -len(str(item[0].get("name") or "")),
            )
        )
        return scored

    def resolve(self, query, *, core=None, account_index=None) -> dict:
        """Match one spoken course name against the pre-filled registry."""
        entries = self.list(core=core, account_index=account_index)
        matches = self.match(query, entries)
        if not matches:
            return {
                "ok": False,
                "code": "course_not_configured",
                "message": f"预先填写的课程表里没有「{str(query or '').strip()}」",
                "candidates": [],
            }
        best_score = matches[0][1]
        top = [item for item in matches if abs(item[1] - best_score) < 0.001]
        distinct = {(item[0]["core"], item[0]["name"]) for item in top}
        if len(distinct) > 1:
            return {
                "ok": False,
                "code": "ambiguous",
                "message": "匹配到多门课程，请说明要刷哪一门，或先在课程表里配置别名",
                "candidates": [item[0] for item in top],
            }
        entry, score = matches[0]
        return {
            "ok": True,
            "entry": dict(entry),
            "score": score,
            "source": "registry",
        }
