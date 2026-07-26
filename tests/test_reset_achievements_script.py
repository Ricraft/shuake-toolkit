import json

import pytest

from scripts.reset_achievements import reset_achievements


def test_reset_clears_achievements_backs_up_and_preserves_other_preferences(tmp_path):
    path = tmp_path / "launcher_preferences.json"
    original = {
        "theme": "tianyi",
        "bgType": "tianyi-flower",
        "tianyiThemeUnlocked": True,
        "tianyiAchievementShown": True,
        "achievements": {
            "tianyi_theme": {"unlockedAt": "2026-07-26T20:00:00"},
            "core_crash": {"unlockedAt": "2026-07-26T21:00:00"},
        },
        "achievementResetToken": 4,
        "tianyiChatHistory": [{"role": "user", "content": "你好"}],
    }
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    result = reset_achievements(path, timestamp="20260726_220000")

    saved = json.loads(path.read_text(encoding="utf-8"))
    backup = json.loads(result["backup_path"].read_text(encoding="utf-8"))
    assert result["previous_count"] == 2
    assert saved["achievements"] == {}
    assert saved["achievementResetToken"] == 5
    assert saved["tianyiAchievementShown"] is False
    assert saved["tianyiThemeUnlocked"] is False
    assert saved["theme"] == "light"
    assert saved["bgType"] == "tianyi-flower"
    assert saved["tianyiChatHistory"] == original["tianyiChatHistory"]
    assert backup == original


def test_reset_can_keep_tianyi_theme_unlock(tmp_path):
    path = tmp_path / "launcher_preferences.json"
    path.write_text(
        json.dumps(
            {
                "theme": "tianyi",
                "tianyiThemeUnlocked": True,
                "tianyiAchievementShown": True,
                "achievements": {"tianyi_theme": True},
            }
        ),
        encoding="utf-8",
    )

    reset_achievements(
        path,
        reset_tianyi_unlock=False,
        create_backup=False,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["achievements"] == {}
    assert saved["tianyiAchievementShown"] is False
    assert saved["tianyiThemeUnlocked"] is True
    assert saved["theme"] == "tianyi"


def test_reset_refuses_to_overwrite_damaged_preferences(tmp_path):
    path = tmp_path / "launcher_preferences.json"
    path.write_text("{damaged", encoding="utf-8")

    with pytest.raises(RuntimeError):
        reset_achievements(path)

    assert path.read_text(encoding="utf-8") == "{damaged"
