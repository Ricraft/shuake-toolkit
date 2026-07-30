import pytest

from src.scheduled_task_service import ScheduledTaskService


class _Timer:
    def __init__(
        self,
        delay,
        target,
        *,
        start_error=None,
        cancel_error=None,
    ):
        self.delay = delay
        self.target = target
        self.start_error = start_error
        self.cancel_error = cancel_error
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True
        if self.start_error:
            raise self.start_error

    def cancel(self):
        self.cancelled = True
        if self.cancel_error:
            raise self.cancel_error

    def fire(self):
        self.target()


def test_scheduled_callback_is_tracked_and_removed_after_completion():
    timers = []

    def factory(delay, target):
        timer = _Timer(delay, target)
        timers.append(timer)
        return timer

    calls = []
    service = ScheduledTaskService(timer_factory=factory)

    timer = service.schedule(250, lambda: calls.append("done"))

    assert timer is timers[0]
    assert timer.delay == 0.25
    assert timer.daemon is True
    assert timer.started is True
    assert service.pending == {timer}

    timer.fire()
    assert calls == ["done"]
    assert service.pending == set()


def test_callback_failure_is_logged_and_does_not_leave_pending_timer():
    timers = []
    logs = []

    def factory(delay, target):
        timer = _Timer(delay, target)
        timers.append(timer)
        return timer

    def broken_callback():
        raise RuntimeError("startup probe failed")

    service = ScheduledTaskService(log=logs.append, timer_factory=factory)
    service.schedule(10, broken_callback)

    timers[0].fire()

    assert service.pending == set()
    assert len(logs) == 1
    assert "broken_callback 执行失败" in logs[0]
    assert "startup probe failed" in logs[0]


def test_timer_start_failure_is_visible_and_not_retained():
    logs = []
    timer = _Timer(0.1, lambda: None, start_error=OSError("thread denied"))
    service = ScheduledTaskService(
        log=logs.append,
        timer_factory=lambda _delay, _target: timer,
    )

    result = service.schedule(100, lambda: None)

    assert result is None
    assert service.pending == set()
    assert len(logs) == 1
    assert "注册失败" in logs[0]
    assert "thread denied" in logs[0]


def test_cancel_all_continues_after_individual_cancel_failure():
    logs = []
    first = _Timer(1, lambda: None, cancel_error=OSError("already closed"))
    second = _Timer(1, lambda: None)
    timers = iter((first, second))
    service = ScheduledTaskService(
        log=logs.append,
        timer_factory=lambda _delay, _target: next(timers),
    )
    service.schedule(1000, lambda: None)
    service.schedule(1000, lambda: None)

    cancelled = service.cancel_all()

    assert cancelled == 2
    assert first.cancelled is True
    assert second.cancelled is True
    assert service.pending == set()
    assert len(logs) == 1
    assert "取消任务失败" in logs[0]


def test_zero_delay_remains_synchronous_and_propagates_failure():
    service = ScheduledTaskService(
        timer_factory=lambda *_args: pytest.fail("不应创建定时器")
    )
    calls = []

    assert service.schedule(0, lambda: calls.append("now")) is None
    assert calls == ["now"]

    with pytest.raises(RuntimeError, match="finish callback failed"):
        service.schedule(
            0,
            lambda: (_ for _ in ()).throw(
                RuntimeError("finish callback failed")
            ),
        )
