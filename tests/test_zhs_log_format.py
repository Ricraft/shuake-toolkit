from src.zhs_log_format import ZhsLogFormatter


def test_progress_lines_get_course_name():
    formatter = ZhsLogFormatter()
    formatter.format("[知到] 新课程: 3D打印技术与应用")
    line = formatter.format("playing 262043 [#################################] 62.5%")
    assert line == "playing 3D打印技术与应用（262043） [#################################] 62.5%"


def test_course_name_switches_between_courses():
    formatter = ZhsLogFormatter()
    formatter.format("[知到] 新课程: 3D打印技术与应用")
    formatter.format("playing 262043 [#####] 30.0%")
    formatter.format("完成课程: 3D打印技术与应用 (12.3s)")
    formatter.format("[知到] 新课程: 解码国家安全")
    line = formatter.format("playing 262099 [#####] 5.0%")
    assert line == "playing 解码国家安全（262099） [#####] 5.0%"


def test_ai_progress_without_video_id_uses_course_name():
    formatter = ZhsLogFormatter()
    formatter.format("[AI] 课程: 人工智能导论")
    line = formatter.format("playing [#####] 12.5%")
    assert line == "playing 人工智能导论 [#####] 12.5%"


def test_answering_and_pause_actions_are_kept():
    formatter = ZhsLogFormatter()
    formatter.format("[知到] 新课程: 军事理论")
    line = formatter.format("answering [#####] 40.0%")
    assert line == "answering 军事理论 [#####] 40.0%"


def test_progress_without_known_course_is_untouched():
    formatter = ZhsLogFormatter()
    line = "playing 78613499 [#####] 32.7%"
    assert formatter.format(line) == line


def test_ansi_colors_are_stripped():
    formatter = ZhsLogFormatter()
    text = formatter.format("\x1b[1m\x1b[95m[AI] 课程: 课程A\x1b[0m")
    assert text == "[AI] 课程: 课程A"
    assert formatter.course_name == "课程A"


def test_completed_course_line_is_not_mistaken_for_course_title():
    formatter = ZhsLogFormatter()
    formatter.format("[知到] 新课程: 课程A")
    formatter.format("完成课程: 课程A (12.3s)")
    assert formatter.course_name == "课程A"


def test_non_progress_lines_pass_through():
    formatter = ZhsLogFormatter()
    for line in ("[知到] 发现 4 门课程", "章节: 第1章", "跳过(已完成): 课程介绍"):
        assert formatter.format(line) == line