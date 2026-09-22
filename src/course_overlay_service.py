"""Crash-safe temporary config overlays used by single-course runs."""

from __future__ import annotations

import importlib
import io
import json
import threading
from collections.abc import Callable
from pathlib import Path

from src.atomic_io import atomic_dump_json, atomic_write_text
from src.config_service import read_ini_config


class CourseOverlayError(RuntimeError):
    """Raised when a temporary config overlay cannot be applied or restored."""


def nested_get(data, path):
    current = data
    for key in path:
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif isinstance(current, list):
            try:
                index = int(key)
            except (TypeError, ValueError):
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def nested_set(data, path, value) -> None:
    if not path:
        raise CourseOverlayError("配置路径为空")
    current = data
    for key in path[:-1]:
        if isinstance(current, dict):
            child = current.get(key)
            if not isinstance(child, (dict, list)):
                child = {}
                current[key] = child
            current = child
        elif isinstance(current, list):
            current = current[int(key)]
        else:
            raise CourseOverlayError("配置路径无法写入")
    last = path[-1]
    if isinstance(current, dict):
        current[last] = value
    elif isinstance(current, list):
        current[int(last)] = value
    else:
        raise CourseOverlayError("配置路径无法写入")


class CourseOverlayService:
    """Apply temporary config edits and always restore them afterwards."""

    def __init__(
        self,
        marker_path: str | Path,
        *,
        logger: Callable[[str], None] | None = None,
        yaml_module=None,
    ):
        self.marker_path = Path(marker_path)
        self._logger = logger or (lambda _message: None)
        self._yaml_module = yaml_module
        self._lock = threading.RLock()

    def _yaml(self):
        if self._yaml_module is None:
            try:
                self._yaml_module = importlib.import_module("yaml")
            except ImportError as exc:
                raise CourseOverlayError("读写 Yatori 配置需要安装 PyYAML") from exc
        return self._yaml_module

    # ---------- marker ----------
    def _read_marker(self) -> dict:
        if not self.marker_path.exists():
            return {"version": 1, "runs": {}}
        try:
            data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._logger(f"[单课程] 恢复标记损坏，已忽略: {exc}")
            return {"version": 1, "runs": {}}
        if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
            return {"version": 1, "runs": {}}
        return data

    def _write_marker(self, runs: dict) -> None:
        if not runs:
            self.marker_path.unlink(missing_ok=True)
            return
        atomic_dump_json(self.marker_path, {"version": 1, "runs": runs})

    def pending(self, core=None):
        runs = self._read_marker().get("runs") or {}
        if core is None:
            return list(runs.keys())
        return [core] if core in runs else []

    def _persist_run(self, core: str, record: dict) -> None:
        with self._lock:
            runs = dict(self._read_marker().get("runs") or {})
            runs[core] = record
            atomic_dump_json(self.marker_path, {"version": 1, "runs": runs})

    def _clear_stale(self, core: str) -> None:
        if self.pending(core) and not self.release(core):
            raise CourseOverlayError(
                "上一次单课程配置尚未恢复，请先恢复后再启动"
            )

    # ---------- Yatori (yaml) ----------
    def apply_yatori(
        self,
        *,
        config_path,
        account_index: int,
        include_courses=None,
        exclude_courses=None,
        chapter_switches=None,
        study_time=None,
    ) -> dict:
        self._clear_stale("yatori")
        path = Path(config_path)
        data = self._load_yaml(path)
        users = nested_get(data, ["users"])
        if not isinstance(users, list):
            raise CourseOverlayError("Yatori 配置缺少 users 列表")
        try:
            index = int(account_index)
        except (TypeError, ValueError) as exc:
            raise CourseOverlayError("账号索引格式错误") from exc
        if index < 0 or index >= len(users):
            raise CourseOverlayError(f"Yatori 账号索引 {index} 不存在")

        custom_path = ["users", index, "coursesCustom"]
        planned = []
        if include_courses is not None:
            planned.append((custom_path + ["includeCourses"], list(include_courses)))
        if exclude_courses is not None:
            planned.append((custom_path + ["excludeCourses"], list(exclude_courses)))
        if study_time is not None:
            planned.append((custom_path + ["studyTime"], str(study_time)))
        for key, value in (chapter_switches or {}).items():
            if key not in ("cxChapterTestSw", "cxWorkSw", "cxExamSw"):
                raise CourseOverlayError(f"不支持的章节开关: {key}")
            planned.append((custom_path + [key], int(value)))

        changes = [
            {
                "path": change_path,
                "original": nested_get(data, change_path),
                "applied": applied,
            }
            for change_path, applied in planned
        ]
        if not changes:
            return {}
        record = {"kind": "yaml", "path": str(path), "changes": changes}
        self._persist_run("yatori", record)
        for change in changes:
            nested_set(data, change["path"], change["applied"])
        self._dump_yaml(path, data)
        return record

    # ---------- Autovisor (ini) ----------
    def apply_ini(
        self,
        *,
        core: str,
        config_path,
        section: str,
        option: str,
        value,
    ) -> dict:
        self._clear_stale(core)
        path = Path(config_path)
        if not path.exists():
            raise CourseOverlayError(f"配置文件不存在: {path}")
        parser = read_ini_config(path)
        if not parser.has_section(section):
            raise CourseOverlayError(f"配置缺少 [{section}] 段")
        original = parser.get(section, option, fallback=None)
        record = {
            "kind": "ini",
            "path": str(path),
            "changes": [
                {
                    "section": section,
                    "option": option,
                    "original": original,
                    "applied": str(value),
                }
            ],
        }
        self._persist_run(core, record)
        parser.set(section, option, str(value))
        self._dump_ini(path, parser)
        return record

    # ---------- restore ----------
    def release(self, core: str) -> bool:
        with self._lock:
            runs = dict(self._read_marker().get("runs") or {})
            record = runs.get(core)
            if not isinstance(record, dict):
                return False
            try:
                if record.get("kind") == "yaml":
                    self._release_yaml(record)
                elif record.get("kind") == "ini":
                    self._release_ini(record)
                else:
                    raise CourseOverlayError(
                        f"未知的恢复类型: {record.get('kind')}"
                    )
            except Exception as exc:
                self._logger(f"[单课程] {core} 配置恢复失败: {exc}")
                return False
            runs.pop(core, None)
            self._write_marker(runs)
            self._logger(f"[单课程] 已恢复 {core} 运行前的课程配置")
            return True

    def release_all(self) -> list[str]:
        released = []
        for core in list((self._read_marker().get("runs") or {}).keys()):
            if self.release(core):
                released.append(core)
        return released

    def _release_yaml(self, record: dict) -> None:
        path = Path(record.get("path") or "")
        if not path.exists():
            self._logger(f"[单课程] 配置文件已不存在，跳过恢复: {path}")
            return
        data = self._load_yaml(path)
        for change in record.get("changes") or []:
            change_path = change.get("path")
            if not change_path:
                continue
            if nested_get(data, change_path) != change.get("applied"):
                self._logger("[单课程] 检测到配置已被改动，保留当前值不覆盖")
                continue
            nested_set(data, change_path, change.get("original"))
        self._dump_yaml(path, data)

    def _release_ini(self, record: dict) -> None:
        path = Path(record.get("path") or "")
        if not path.exists():
            self._logger(f"[单课程] 配置文件已不存在，跳过恢复: {path}")
            return
        parser = read_ini_config(path)
        for change in record.get("changes") or []:
            section = change.get("section")
            option = change.get("option")
            if not section or not option or not parser.has_section(section):
                continue
            if parser.get(section, option, fallback=None) != change.get("applied"):
                self._logger("[单课程] 检测到配置已被改动，保留当前值不覆盖")
                continue
            original = change.get("original")
            if original is None:
                parser.remove_option(section, option)
            else:
                parser.set(section, option, str(original))
        self._dump_ini(path, parser)

    # ---------- IO ----------
    def _load_yaml(self, path) -> dict:
        yaml = self._yaml()
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception as exc:
            raise CourseOverlayError(f"读取 {path} 失败: {exc}") from exc
        if not isinstance(data, dict):
            raise CourseOverlayError(f"{path} 内容不是对象")
        return data

    def _dump_yaml(self, path, data) -> None:
        yaml = self._yaml()
        buffer = io.StringIO()
        yaml.safe_dump(data, buffer, allow_unicode=True, sort_keys=False)
        atomic_write_text(path, buffer.getvalue())

    def _dump_ini(self, path, parser) -> None:
        buffer = io.StringIO()
        parser.write(buffer)
        atomic_write_text(path, buffer.getvalue())
