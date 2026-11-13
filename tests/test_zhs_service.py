from types import SimpleNamespace

import pytest

from src.zhs_service import ZhsService, course_page_url, parse_captcha_line


def make_service(tmp_path):
    logs = []

    def log(source, message, **_kwargs):
        logs.append((source, message))

    launcher = SimpleNamespace(
        get_base_dir=lambda: str(tmp_path),
        log=log,
    )
    return ZhsService(launcher), logs


def zhidao(course_id, name="课程"):
    return {
        "type": "zhidao",
        "course_id": str(course_id),
        "class_id": "",
        "key": f"zhidao:{course_id}:",
        "label": name,
    }


def ai(course_id, class_id):
    return {
        "type": "ai",
        "course_id": str(course_id),
        "class_id": str(class_id),
        "key": f"ai:{course_id}:{class_id}",
        "label": "AI 课程",
    }


def test_multiple_zhidao_courses_are_passed_to_a_single_invocation(tmp_path):
    service, _logs = make_service(tmp_path)
    command = service.build_command(
        "play", [zhidao(1000006293), zhidao(1000006282)],
    )
    assert command == [
        "-m", "zhs", "play", "--type", "zhidao",
        "-c", "1000006293", "-c", "1000006282",
    ]


def test_course_key_strings_are_accepted_as_fallback(tmp_path):
    service, _logs = make_service(tmp_path)
    command = service.build_command(
        "play", ["zhidao:1000006293:", "zhidao:1000006282:"],
    )
    assert command == [
        "-m", "zhs", "play", "--type", "zhidao",
        "-c", "1000006293", "-c", "1000006282",
    ]


def test_single_ai_course_uses_ai_course_options(tmp_path):
    service, _logs = make_service(tmp_path)
    command = service.build_command("play", [ai(111, 222)])
    assert command == [
        "-m", "zhs", "play", "--ai-course", "111", "--ai-class", "222",
    ]


def test_multiple_ai_courses_use_composite_course_ids(tmp_path):
    service, _logs = make_service(tmp_path)
    command = service.build_command("play", [ai(111, 222), ai(333, 444)])
    assert command == [
        "-m", "zhs", "play", "--type", "ai",
        "-c", "111:222", "-c", "333:444",
    ]


def test_mixed_types_omit_explicit_type_so_zhs_can_auto_detect(tmp_path):
    service, _logs = make_service(tmp_path)
    command = service.build_command("play", [zhidao(1000006293), ai(111, 222)])
    assert command == [
        "-m", "zhs", "play",
        "-c", "1000006293", "-c", "111:222",
    ]


def test_exam_rejects_mixed_course_types(tmp_path):
    service, _logs = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.build_command("exam", [zhidao(1000006293), ai(111, 222)])


def test_exam_rejects_multiple_ai_courses(tmp_path):
    service, _logs = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.build_command("exam", [ai(111, 222), ai(333, 444)])


def test_exam_supports_multiple_zhidao_courses_with_submit(tmp_path):
    service, _logs = make_service(tmp_path)
    command = service.build_command(
        "exam", [zhidao(1000006293), zhidao(1000006282)], submit_exam=True,
    )
    assert command == [
        "-m", "zhs", "exam", "--type", "zhidao",
        "-c", "1000006293", "-c", "1000006282", "--submit",
    ]


def test_homework_without_selection_falls_back_to_auto(tmp_path):
    service, _logs = make_service(tmp_path)
    assert service.build_command("homework", None) == [
        "-m", "zhs", "homework", "--type", "auto",
    ]


def test_unrecognized_course_arguments_are_ignored_with_log(tmp_path):
    service, logs = make_service(tmp_path)
    command = service.build_command("play", [zhidao(1000006293), "garbage", 42, None])
    assert command == [
        "-m", "zhs", "play", "-c", "1000006293", "--type", "zhidao",
    ]
    assert any("无法识别" in message for _source, message in logs)


def test_ai_course_without_class_id_is_skipped(tmp_path):
    service, logs = make_service(tmp_path)
    broken = {"type": "ai", "course_id": "111", "class_id": ""}
    command = service.build_command("play", [zhidao(1000006293), broken, ai(333, 444)])
    assert "-c" in command
    assert "111" not in command
    assert any("classId" in message for _source, message in logs)


def make_launch_launcher(tmp_path):
    """构造一个最小的 launcher，用来验证 start_zhs 里命令是否真的按勾选生成。"""
    started = []
    logs = []

    class FakeProcessService:
        def start(self, **kwargs):
            started.append(kwargs)
            return True

    launcher = SimpleNamespace(
        running={"zhs": False},
        starting={"zhs": False},
        stop_requested={"zhs": False},
        _last_start_error={},
        _last_start_failure_kind={},
        get_base_dir=lambda: str(tmp_path),
        log=lambda source, message, **_kw: logs.append((source, message)),
        log_system=lambda message, **_kw: logs.append(("system", message)),
        _get_zhs_service=None,
        _reject_runtime_start=lambda core, message, **kw: (
            logs.append((core, message)) or False
        ),
        _claim_runtime_start=lambda core: True,
        _get_runtime_process_service=lambda: FakeProcessService(),
        _build_encoding_candidates=lambda *names: tuple(names),
    )
    service = ZhsService(launcher)
    service.environment_state = lambda **_kw: {"ready": True, "version": "0.1.5"}
    service.session_state = lambda **_kw: {"logged_in": True}
    launcher._get_zhs_service = lambda: service
    launcher.started = started
    launcher.logs = logs
    return launcher


def test_start_zhs_passes_every_selected_course_to_the_process(tmp_path):
    from src.core_launch_service import CoreLaunchService

    launcher = make_launch_launcher(tmp_path)
    started = CoreLaunchService(launcher).start_zhs(
        action="play", courses=[zhidao(1000006293), zhidao(1000006282)],
    )
    assert started is True
    command = launcher.started[0]["command"]
    assert command[1:] == [
        "-m", "zhs", "play", "--type", "zhidao",
        "-c", "1000006293", "-c", "1000006282",
    ]


def test_start_zhs_starts_runtime_prepare_when_environment_missing(tmp_path):
    """环境缺失时不应该只报错：顺手在后台准备 venv-zhs，用户重试即可。"""
    from src.core_launch_service import CoreLaunchService

    launcher = make_launch_launcher(tmp_path)
    requested = []
    launcher._request_zhs_runtime_prepare = (
        lambda **kwargs: requested.append(kwargs) or True
    )
    launcher._get_zhs_service().environment_state = lambda **_kw: {
        "ready": False,
        "message": "未找到 ZHS 运行环境（.runtime/venv-zhs）",
    }

    started = CoreLaunchService(launcher).start_zhs(
        action="play", courses=[zhidao(1000006293)],
    )

    assert started is False
    assert requested == [{"force": True}]
    assert any(
        "正在后台准备" in message for _source, message in launcher.logs
    )


def test_start_zhs_rejects_exam_with_mixed_course_types(tmp_path):
    from src.core_launch_service import CoreLaunchService

    launcher = make_launch_launcher(tmp_path)
    started = CoreLaunchService(launcher).start_zhs(
        action="exam", courses=[zhidao(1000006293), ai(111, 222)], submit_exam=True,
    )
    assert started is False
    assert launcher.started == []
    assert any("考试模式" in message for _source, message in launcher.logs)

def test_course_url_retry_only_supports_play(tmp_path):
    service, _logs = make_service(tmp_path)
    url = "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=abc"
    with pytest.raises(ValueError):
        service.build_command("homework", [], url=url)


def test_course_url_retry_rejects_non_http_target(tmp_path):
    service, _logs = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.build_command("play", [], url="not-a-url")


def test_course_url_retry_builds_url_option(tmp_path):
    service, _logs = make_service(tmp_path)
    url = "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=4e5f5a"
    command = service.build_command("play", [], url=url)
    assert command == ["-m", "zhs", "play", "--url", url]


def test_parse_captcha_line_extracts_course_and_video():
    line = (
        "[21:34:00] WARNING zhs.zhidao.video:play_course:87 | Course stopped: "
        "captcha required (racId=4e5f5a5944594859454a585958435b4a5b, "
        "course=3D打印技术与应用, videoId=262043)"
    )
    hint = parse_captcha_line(line)
    assert hint["course"] == "3D打印技术与应用"
    assert hint["racId"] == "4e5f5a5944594859454a585958435b4a5b"
    assert hint["videoId"] == "262043"
    assert hint["url"] == (
        "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId="
        "4e5f5a5944594859454a585958435b4a5b"
    )
    assert "验证码" in hint["message"]


def test_parse_captcha_line_ignores_other_lines():
    assert parse_captcha_line("playing 78613499 [#####   ] 32.7%") is None
    assert parse_captcha_line("") is None
    assert parse_captcha_line(None) is None


def test_course_page_url_uses_recruit_and_course_id():
    assert course_page_url("abc") == (
        "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=abc"
    )
