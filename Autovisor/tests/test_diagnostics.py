from Autovisor.modules.diagnostics import RateLimitedDiagnostics, summarize_exception


class _Logger:
    def __init__(self):
        self.warnings = []

    def warn(self, message):
        self.warnings.append(message)


def test_exception_summary_is_single_line_and_bounded():
    summary = summarize_exception(RuntimeError("first\n" + "x" * 300), limit=40)

    assert "\n" not in summary
    assert summary.startswith("RuntimeError: first ")
    assert len(summary) <= len("RuntimeError: ") + 40
    assert summary.endswith("…")


def test_repeated_diagnostic_is_limited_by_key_and_interval():
    ticks = iter([0, 10, 31])
    logger = _Logger()
    diagnostics = RateLimitedDiagnostics(
        logger,
        interval_seconds=30,
        clock=lambda: next(ticks),
    )

    assert diagnostics.warn("video", "读取失败", ValueError("one")) is True
    assert diagnostics.warn("video", "读取失败", ValueError("two")) is False
    assert diagnostics.warn("video", "读取失败", ValueError("three")) is True

    assert logger.warnings == [
        "读取失败: ValueError: one",
        "读取失败: ValueError: three",
    ]


def test_diagnostic_keys_are_independent_and_clear_resets_state():
    logger = _Logger()
    diagnostics = RateLimitedDiagnostics(logger, clock=lambda: 5)

    assert diagnostics.warn("position", "位置", OSError("a")) is True
    assert diagnostics.warn("resume", "恢复", OSError("b")) is True
    assert diagnostics.warn("position", "位置", OSError("c")) is False
    diagnostics.clear()
    assert diagnostics.warn("position", "位置", OSError("d")) is True
