"""Persistent Web preferences with transactional side-effect rollback."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from threading import RLock

from src.atomic_io import atomic_dump_json


DEFAULT_PREFERENCES = {
    "theme": "dark",
    "autoStart": False,
    "autoShutdown": False,
    "autoRun": False,
    "minimizeToTray": False,
    "startMinimized": False,
    "alwaysOnTop": False,
    "notifyOnComplete": True,
    "notifyOnError": True,
    "soundEnabled": True,
    "rememberGeometry": False,
    "autoCleanLogs": False,
    "qb_external_url": "",
    "tianyiThemeUnlocked": False,
    "tianyiAchievementShown": False,
    "tianyiChatHistory": [],
    "achievements": {},
    "achievementResetToken": 0,
}


class PreferencesService:
    """Load, save and update launcher preferences as one recoverable action."""

    def __init__(
        self,
        path: str | Path,
        *,
        log: Callable[[str], None] | None = None,
        apply_side_effects: Callable[[dict], list[str] | None] | None = None,
        rollback_side_effects: Callable[[dict, dict], list[str] | None] | None = None,
        writer: Callable[..., None] = atomic_dump_json,
    ):
        self.path = Path(path)
        self.log = log or (lambda _message: None)
        self.apply_side_effects = apply_side_effects or (lambda _payload: [])
        self.rollback_side_effects = rollback_side_effects or (
            lambda _previous, _payload: []
        )
        self.writer = writer
        self._lock = RLock()
        self.values = self._load()

    def _load(self) -> dict:
        values = deepcopy(DEFAULT_PREFERENCES)
        try:
            if self.path.exists():
                stored = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    values.update(deepcopy(stored))
        except Exception as exc:
            self.log(f"加载启动器偏好失败: {exc}")
        return values

    def _replace_values(self, values: dict) -> None:
        self.values.clear()
        self.values.update(deepcopy(values))

    def save(self) -> bool:
        with self._lock:
            try:
                self.writer(self.path, deepcopy(self.values))
                return True
            except Exception as exc:
                self.log(f"保存启动器偏好失败: {exc}")
                return False

    def get(self) -> dict:
        with self._lock:
            return deepcopy(self.values)

    def enabled(self, key: str, default: bool = False) -> bool:
        with self._lock:
            return bool(self.values.get(key, default))

    @staticmethod
    def _failure_list(value) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value if str(item)]

    def update(self, payload) -> dict:
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "message": "偏好设置格式错误",
                "preferences": self.get(),
            }

        try:
            normalized_payload = deepcopy(payload)
        except Exception as exc:
            return {
                "ok": False,
                "message": f"偏好设置内容无效: {exc}",
                "preferences": self.get(),
            }

        with self._lock:
            previous = self.get()
            self.values.update(normalized_payload)
            if not self.save():
                self._replace_values(previous)
                return {
                    "ok": False,
                    "message": "偏好设置写入失败，已恢复原值",
                    "preferences": self.get(),
                }

            try:
                failures = self._failure_list(
                    self.apply_side_effects(deepcopy(normalized_payload))
                )
            except Exception as exc:
                failures = [f"偏好设置应用失败: {exc}"]

            if not failures:
                return {"ok": True, "preferences": self.get()}

            self._replace_values(previous)
            if not self.save():
                failures.append("偏好文件恢复失败")
            try:
                failures.extend(
                    self._failure_list(
                        self.rollback_side_effects(
                            deepcopy(previous),
                            deepcopy(normalized_payload),
                        )
                    )
                )
            except Exception as exc:
                failures.append(f"偏好运行状态恢复失败: {exc}")
            return {
                "ok": False,
                "message": "；".join(failures),
                "preferences": self.get(),
            }
