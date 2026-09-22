# encoding=utf-8
"""课程表（预先填写）读取、保存与中文名称匹配。"""

import json

from src.course_plan_service import (
    CoursePlanService,
    clean_name_list,
    normalize_course_name,
    normalize_entry,
)


def _service(tmp_path):
    return CoursePlanService(tmp_path / "course_plans.json")


def test_normalize_folds_width_case_space_and_punctuation():
    assert normalize_course_name("高数 A2") == normalize_course_name("高数A2")
    assert normalize_course_name("高等数学Ａ２") == normalize_course_name("高等数学a2")
    assert normalize_course_name("  高数-A2  ") == normalize_course_name("高数A2")
    assert normalize_course_name("") == ""


def test_clean_name_list_accepts_lines_commas_and_lists():
    assert clean_name_list("高数A2\n线代, 概率论") == [
        "高数A2",
        "线代",
        "概率论",
    ]
    assert clean_name_list(["高数A2", "高数A2", "  "]) == ["高数A2"]


def test_normalize_entry_rejects_missing_name_or_core():
    assert normalize_entry({"name": "高数A2"}) is None
    assert normalize_entry({"core": "yatori"}) is None
    assert normalize_entry({"name": "高数A2", "core": "unknown"}) is None
    entry = normalize_entry({"name": "高数A2", "core": "yatori", "aliases": "高数, 高数A2"})
    assert entry["aliases"] == ["高数", "高数A2"]
    assert entry["accountIndex"] == 0
    assert entry["skipQuestions"] is False


def test_save_then_load_round_trip(tmp_path):
    service = _service(tmp_path)
    result = service.save(
        {
            "courses": [
                {"name": "高等数学A2", "aliases": "高数A2", "core": "yatori"},
                {
                    "name": "大学物理",
                    "core": "autovisor",
                    "courseUrl": "https://example.test/course/2",
                    "skipQuestions": True,
                    "maxMinutes": "45",
                },
            ]
        }
    )
    assert result["ok"] is True
    loaded = service.load()
    assert [item["name"] for item in loaded["courses"]] == ["高等数学A2", "大学物理"]
    assert loaded["courses"][0]["aliases"] == ["高数A2"]
    assert loaded["courses"][1]["skipQuestions"] is True
    assert loaded["courses"][1]["maxMinutes"] == "45"


def test_save_rejects_broken_entry_without_touching_file(tmp_path):
    service = _service(tmp_path)
    assert service.save({"courses": [{"name": "高等数学A2", "core": "yatori"}]})["ok"] is True
    before = (tmp_path / "course_plans.json").read_text(encoding="utf-8")

    result = service.save({"courses": [{"name": "", "core": "yatori"}]})
    assert result["ok"] is False
    assert "第 1 门课程" in result["message"]
    assert (tmp_path / "course_plans.json").read_text(encoding="utf-8") == before


def test_damaged_registry_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "course_plans.json"
    path.write_text("{not json", encoding="utf-8")
    assert CoursePlanService(path).load() == {"version": 1, "courses": []}


def test_resolve_prefers_exact_name_then_alias(tmp_path):
    service = _service(tmp_path)
    service.save(
        {
            "courses": [
                {"name": "高等数学A2", "aliases": "高数A2, 高数2", "core": "yatori"},
                {"name": "线性代数", "core": "yatori"},
            ]
        }
    )
    exact = service.resolve("高等数学A2")
    assert exact["ok"] is True
    assert exact["entry"]["name"] == "高等数学A2"
    assert exact["source"] == "registry"

    alias = service.resolve("高数 A2")
    assert alias["ok"] is True
    assert alias["entry"]["name"] == "高等数学A2"


def test_resolve_reports_missing_course(tmp_path):
    service = _service(tmp_path)
    service.save({"courses": [{"name": "线性代数", "core": "yatori"}]})
    result = service.resolve("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "course_not_configured"
    assert result["candidates"] == []


def test_resolve_reports_ambiguity_across_cores(tmp_path):
    service = _service(tmp_path)
    service.save(
        {
            "courses": [
                {"name": "高等数学A2", "core": "yatori"},
                {"name": "高等数学A2", "core": "autovisor"},
            ]
        }
    )
    result = service.resolve("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "ambiguous"
    assert len(result["candidates"]) == 2


def test_resolve_can_be_scoped_to_one_core(tmp_path):
    service = _service(tmp_path)
    service.save(
        {
            "courses": [
                {"name": "高等数学A2", "core": "yatori"},
                {"name": "高等数学A2", "core": "autovisor"},
            ]
        }
    )
    result = service.resolve("高等数学A2", core="autovisor")
    assert result["ok"] is True
    assert result["entry"]["core"] == "autovisor"


def test_list_filters_by_account_index(tmp_path):
    service = _service(tmp_path)
    service.save(
        {
            "courses": [
                {"name": "高等数学A2", "core": "yatori", "accountIndex": 0},
                {"name": "大学物理", "core": "yatori", "accountIndex": 1},
            ]
        }
    )
    assert [item["name"] for item in service.list(account_index=1)] == ["大学物理"]
    assert len(service.list(core="yatori")) == 2
