import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright._impl._errors import TargetClosedError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.course_queue import run_course_queue
from modules.course_session import CourseAuthenticationError

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []
        self.logs = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def write_log(self, message):
        self.logs.append(message)


class _Session:
    def __init__(self, url, failures):
        self.url = url
        self.failures = failures
        self.profile = SimpleNamespace(
            working_loop_options=lambda: {"is_new_version": url.endswith("new")}
        )

    async def open(self, *_args):
        failure = self.failures.get((self.url, "open"))
        if failure:
            raise failure


def _factory(failures):
    return lambda url: _Session(url, failures)


def test_course_queue_continues_after_local_failure_and_reports_partial_result():
    urls = [
        "https://studyvideoh5.zhihuishu.com/first",
        "https://fusioncourseh5.zhihuishu.com/broken",
        "https://hike.zhihuishu.com/new",
    ]
    calls = []

    async def worker(_page, *, course_url, **options):
        calls.append((course_url, options))
        if "broken" in course_url:
            raise RuntimeError("selector changed")

    logger = _Logger()
    summary = asyncio.run(
        run_course_queue(
            object(),
            SimpleNamespace(course_urls=urls),
            logger,
            course_worker=worker,
            session_factory=_factory({}),
        )
    )

    assert (summary.total, summary.completed, summary.failed) == (3, 2, 1)
    assert [call[0] for call in calls] == urls
    assert "fusioncourseh5.zhihuishu.com" in logger.errors[0]
    assert "selector changed" in logger.errors[0]
    assert logger.logs


@pytest.mark.parametrize(
    "fatal_error",
    [CourseAuthenticationError("expired"), TargetClosedError("closed")],
)
def test_course_queue_stops_immediately_for_fatal_errors(fatal_error):
    urls = ["https://studyvideoh5.zhihuishu.com/first", "https://hike.zhihuishu.com/second"]
    attempted = []

    def session_factory(url):
        attempted.append(url)
        return _Session(url, {(url, "open"): fatal_error})

    with pytest.raises(type(fatal_error)):
        asyncio.run(
            run_course_queue(
                object(),
                SimpleNamespace(course_urls=urls),
                _Logger(),
                course_worker=lambda *_args, **_kwargs: None,
                session_factory=session_factory,
            )
        )
    assert attempted == [urls[0]]


def test_course_queue_reports_complete_success():
    urls = ["https://studyvideoh5.zhihuishu.com/first"]

    async def worker(*_args, **_kwargs):
        return None

    summary = asyncio.run(
        run_course_queue(
            object(),
            SimpleNamespace(course_urls=urls),
            _Logger(),
            course_worker=worker,
            session_factory=_factory({}),
        )
    )
    assert summary.is_complete is True
