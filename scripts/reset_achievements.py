"""Reset launcher achievements for repeatable UI testing.

Run this script while the launcher is closed. It only edits the launcher
preferences file and creates a timestamped backup before changing it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFERENCES_PATH = PROJECT_ROOT / "data" / "launcher_preferences.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.atomic_io import atomic_dump_json


def _load_preferences(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取偏好文件，已停止重置：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("偏好文件的根节点不是对象，已停止重置")
    return payload


def reset_achievements(
    path: Path = DEFAULT_PREFERENCES_PATH,
    *,
    reset_tianyi_unlock: bool = True,
    create_backup: bool = True,
    timestamp: str | None = None,
) -> dict:
    """Clear achievement records and return a summary of the operation."""
    path = Path(path)
    preferences = _load_preferences(path)
    raw_achievements = preferences.get("achievements")
    previous_count = (
        len(raw_achievements)
        if isinstance(raw_achievements, (dict, list))
        else 0
    )

    backup_path = None
    if create_backup and path.exists():
        stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.before-achievement-reset-{stamp}{path.suffix}")
        shutil.copy2(path, backup_path)

    try:
        previous_token = int(preferences.get("achievementResetToken", 0) or 0)
    except (TypeError, ValueError):
        previous_token = 0

    preferences["achievements"] = {}
    preferences["achievementResetToken"] = max(0, previous_token) + 1
    preferences["tianyiAchievementShown"] = False

    theme_changed = False
    if reset_tianyi_unlock:
        preferences["tianyiThemeUnlocked"] = False
        if preferences.get("theme") == "tianyi":
            preferences["theme"] = "light"
            theme_changed = True

    atomic_dump_json(path, preferences)
    return {
        "path": path,
        "backup_path": backup_path,
        "previous_count": previous_count,
        "reset_token": preferences["achievementResetToken"],
        "tianyi_unlock_reset": reset_tianyi_unlock,
        "theme_changed": theme_changed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重置统一启动器的成就状态")
    parser.add_argument(
        "--preferences",
        type=Path,
        default=DEFAULT_PREFERENCES_PATH,
        help="偏好文件路径，默认使用项目 data/launcher_preferences.json",
    )
    parser.add_argument(
        "--keep-tianyi-theme",
        action="store_true",
        help="保留洛天依主题解锁状态，仅清空成就记录",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="不创建重置前的偏好文件备份",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = reset_achievements(
            args.preferences,
            reset_tianyi_unlock=not args.keep_tianyi_theme,
            create_backup=not args.no_backup,
        )
    except Exception as exc:
        print(f"[失败] {exc}")
        return 1

    print(f"[完成] 已清空 {result['previous_count']} 项成就记录")
    print(f"偏好文件：{result['path']}")
    if result["backup_path"]:
        print(f"备份文件：{result['backup_path']}")
    if result["tianyi_unlock_reset"]:
        print("洛天依主题解锁触发条件也已重置，可重新点击洛天依测试")
    if result["theme_changed"]:
        print("原主题为洛天依主题，已切换为白天模式；壁纸设置保持不变")
    print("请重新打开统一启动器以载入重置后的状态")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
