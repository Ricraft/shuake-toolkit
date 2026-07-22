from src.runtime_activity import summarize_autovisor_activity


def test_empty_activity_is_idle_until_runtime_starts():
    assert summarize_autovisor_activity([])["phase"] == "idle"
    assert summarize_autovisor_activity([], starting=True)["phase"] == "starting"
    assert summarize_autovisor_activity([], running=True)["phase"] == "starting"


def test_course_title_position_and_progress_are_extracted():
    activity = summarize_autovisor_activity(
        [
            "[10:00:00] [INFO] 程序启动中...",
            "[10:00:01] [INFO] 开始处理第 2/4 门课程（example.com）",
            "[10:00:02] [INFO] 当前课程:<<大学生安全教育>>，是新版课程",
            "[10:00:03] 完成进度: 87%",
        ],
        running=True,
    )

    assert activity == {
        "phase": "video",
        "label": "正在播放课程",
        "progress_percent": 87,
        "course": "大学生安全教育",
        "course_index": 2,
        "course_total": 4,
        "last_error": None,
    }


def test_test_phase_keeps_course_progress_context():
    activity = summarize_autovisor_activity(
        [
            "开始处理第 1/3 门课程（example.com）",
            "当前课程:<<高等数学>>",
            "完成进度: 64%",
            "[START] 开始答题，共 10 道题",
        ],
        running=True,
    )

    assert activity["phase"] == "test"
    assert activity["label"] == "正在处理测验"
    assert activity["progress_percent"] == 64
    assert activity["course"] == "高等数学"


def test_completed_and_unexpected_stop_are_distinguished():
    completed = summarize_autovisor_activity(
        ["当前课程:<<英语>>", "所有课程已学习完毕!"]
    )
    stopped = summarize_autovisor_activity(["当前课程:<<英语>>", "完成进度: 40%"])

    assert completed["phase"] == "completed"
    assert completed["progress_percent"] == 100
    assert stopped["phase"] == "stopped"
    assert stopped["progress_percent"] == 40


def test_failure_message_is_bounded_and_drops_url_query():
    activity = summarize_autovisor_activity(
        [
            "[12:00:00] [ERROR] 第 1/2 门课程执行失败: "
            "https://example.com/course?token=secret&user=123 " + "x" * 200
        ]
    )

    assert activity["phase"] == "failed"
    assert "token=secret" not in activity["last_error"]
    assert len(activity["last_error"]) <= 160


def test_new_start_discards_previous_terminal_state():
    activity = summarize_autovisor_activity(
        [
            "[ERROR] 系统出错,请检查后重新启动!",
            "所有课程已学习完毕!",
            "正在启动任务...",
        ],
        starting=True,
    )

    assert activity["phase"] == "starting"
    assert activity["last_error"] is None
    assert activity["course"] is None


def test_nonzero_process_exit_is_rendered_as_failure():
    activity = summarize_autovisor_activity(
        ["[ERROR] Autovisor 已退出，返回码: 3"]
    )

    assert activity["phase"] == "failed"
    assert activity["label"] == "运行失败"
    assert activity["last_error"] == "Autovisor 已退出，返回码: 3"
