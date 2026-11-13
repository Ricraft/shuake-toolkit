"""zhs 以 0 退出时的「课程未完成」判定。"""

import 统一启动器 as launcher_module


def make_launcher():
    launcher = launcher_module.UnifiedLauncher.__new__(
        launcher_module.UnifiedLauncher
    )
    launcher._zhs_run_had_failure = False
    launcher._zhs_captcha_hint = None
    launcher._zhs_log_formatter = launcher_module.ZhsLogFormatter()
    launcher.log_history = {"zhs": [], "system": []}
    launcher.log = lambda *args, **kwargs: None
    launcher.log_system = lambda *args, **kwargs: None
    return launcher


def test_course_failure_marks_run_incomplete():
    launcher = make_launcher()

    launcher._observe_zhs_log(
        "[知到] 知到课程 解码国家安全 处理失败: [Errno 22] Invalid argument"
    )

    assert launcher._zhs_run_had_failure is True
    assert launcher._zhs_captcha_hint is None


def test_course_list_failure_marks_run_incomplete():
    launcher = make_launcher()

    launcher._observe_zhs_log("[知到] 获取知到课程列表失败: 登录已过期")

    assert launcher._zhs_run_had_failure is True


def test_captcha_marks_run_incomplete_and_records_hint():
    launcher = make_launcher()

    launcher._observe_zhs_log(
        "WARNING Course stopped: captcha required "
        "(racId=4e5f5a5944594859454a585958435b4a5b, "
        "course=3D打印技术与应用, videoId=262043)"
    )

    assert launcher._zhs_run_had_failure is True
    assert launcher._zhs_captcha_hint["videoId"] == "262043"


def test_normal_progress_keeps_run_clean():
    launcher = make_launcher()

    launcher._observe_zhs_log("[知到] 新课程: 3D打印技术与应用")
    launcher._observe_zhs_log("playing 262043 [######     ] 62.5%")
    launcher._observe_zhs_log("完成课程: 3D打印技术与应用 (724.4s)")

    assert launcher._zhs_run_had_failure is False
