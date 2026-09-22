# encoding=utf-8
"""单课程临时配置覆盖必须可写、可恢复、可崩溃恢复。"""

import json

import pytest
import yaml

from src.course_overlay_service import (
    CourseOverlayError,
    CourseOverlayService,
    nested_get,
)


def _write_yatori(tmp_path, *, include=None, study_time="", switches=(1, 1, 1)):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "setting": {"basicSetting": {"logLevel": "INFO"}},
                "users": [
                    {
                        "accountType": "XUEXITONG",
                        "account": "10001",
                        "coursesCustom": {
                            "studyTime": study_time,
                            "cxChapterTestSw": switches[0],
                            "cxWorkSw": switches[1],
                            "cxExamSw": switches[2],
                            "includeCourses": list(include or []),
                            "excludeCourses": [],
                        },
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_ini(tmp_path, *, limit="30"):
    path = tmp_path / "configs.ini"
    path.write_text(
        "[user-account]\nusername = 10001\npassword = x\n"
        "\n[course-option]\nlimitMaxTime = %s\nlimitSpeed = 1.0\n" % limit,
        encoding="utf-8",
    )
    return path


def test_apply_yatori_writes_single_course_and_switches(tmp_path):
    config = _write_yatori(tmp_path)
    service = CourseOverlayService(tmp_path / "overlay.json")

    service.apply_yatori(
        config_path=config,
        account_index=0,
        include_courses=["高等数学A2"],
        exclude_courses=[],
        chapter_switches={"cxChapterTestSw": 0, "cxWorkSw": 0, "cxExamSw": 0},
        study_time="45",
    )

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    custom = data["users"][0]["coursesCustom"]
    assert custom["includeCourses"] == ["高等数学A2"]
    assert custom["cxChapterTestSw"] == 0
    assert custom["cxWorkSw"] == 0
    assert custom["cxExamSw"] == 0
    assert custom["studyTime"] == "45"
    assert service.pending("yatori") == ["yatori"]

    assert service.release("yatori") is True
    restored = yaml.safe_load(config.read_text(encoding="utf-8"))
    custom = restored["users"][0]["coursesCustom"]
    assert custom["includeCourses"] == []
    assert custom["cxChapterTestSw"] == 1
    assert custom["studyTime"] == ""
    assert service.pending() == []


def test_release_keeps_user_edits_made_during_run(tmp_path):
    config = _write_yatori(tmp_path)
    service = CourseOverlayService(tmp_path / "overlay.json")
    service.apply_yatori(
        config_path=config,
        account_index=0,
        include_courses=["高等数学A2"],
        chapter_switches={"cxWorkSw": 0},
    )

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["users"][0]["coursesCustom"]["includeCourses"] = ["用户后来改的课"]
    config.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    assert service.release("yatori") is True
    restored = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert restored["users"][0]["coursesCustom"]["includeCourses"] == ["用户后来改的课"]
    assert restored["users"][0]["coursesCustom"]["cxWorkSw"] == 1


def test_stale_overlay_is_recovered_by_a_new_service_instance(tmp_path):
    config = _write_yatori(tmp_path)
    marker = tmp_path / "overlay.json"
    CourseOverlayService(marker).apply_yatori(
        config_path=config,
        account_index=0,
        include_courses=["高等数学A2"],
    )
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]["includeCourses"] == ["高等数学A2"]

    recovered = CourseOverlayService(marker)
    assert recovered.release_all() == ["yatori"]
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]["includeCourses"] == []
    assert not marker.exists()


def test_second_apply_releases_the_first_overlay(tmp_path):
    config = _write_yatori(tmp_path, include=["旧课"])
    service = CourseOverlayService(tmp_path / "overlay.json")
    service.apply_yatori(config_path=config, account_index=0, include_courses=["A"])
    service.apply_yatori(config_path=config, account_index=0, include_courses=["B"])

    assert yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]["includeCourses"] == ["B"]
    assert service.release("yatori") is True
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]["includeCourses"] == ["旧课"]


def test_apply_yatori_rejects_bad_account_index(tmp_path):
    config = _write_yatori(tmp_path)
    service = CourseOverlayService(tmp_path / "overlay.json")
    with pytest.raises(CourseOverlayError):
        service.apply_yatori(config_path=config, account_index=5, include_courses=["A"])
    assert service.pending() == []


def test_apply_ini_sets_and_restores_limit(tmp_path):
    config = _write_ini(tmp_path, limit="30")
    service = CourseOverlayService(tmp_path / "overlay.json")

    service.apply_ini(
        core="autovisor",
        config_path=config,
        section="course-option",
        option="limitMaxTime",
        value="45",
    )
    text = config.read_text(encoding="utf-8")
    assert "limitMaxTime = 45" in text

    assert service.release("autovisor") is True
    assert "limitMaxTime = 30" in config.read_text(encoding="utf-8")


def test_apply_ini_removes_option_that_did_not_exist(tmp_path):
    config = tmp_path / "configs.ini"
    config.write_text("[course-option]\nlimitSpeed = 1.0\n", encoding="utf-8")
    service = CourseOverlayService(tmp_path / "overlay.json")

    service.apply_ini(
        core="autovisor",
        config_path=config,
        section="course-option",
        option="limitMaxTime",
        value="45",
    )
    assert "limitMaxTime = 45" in config.read_text(encoding="utf-8")

    assert service.release("autovisor") is True
    assert "limitMaxTime" not in config.read_text(encoding="utf-8")


def test_apply_ini_requires_existing_section(tmp_path):
    config = tmp_path / "configs.ini"
    config.write_text("[user-account]\nusername = x\n", encoding="utf-8")
    service = CourseOverlayService(tmp_path / "overlay.json")
    with pytest.raises(CourseOverlayError):
        service.apply_ini(
            core="autovisor",
            config_path=config,
            section="course-option",
            option="limitMaxTime",
            value="45",
        )


def test_nested_get_walks_dicts_and_lists():
    data = {"users": [{"coursesCustom": {"includeCourses": ["A"]}}]}
    assert nested_get(data, ["users", 0, "coursesCustom", "includeCourses"]) == ["A"]
    assert nested_get(data, ["users", 3, "name"]) is None
    assert nested_get(data, ["users", "x", "name"]) is None
