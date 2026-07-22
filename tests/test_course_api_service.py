import io
import json
from pathlib import Path
from types import SimpleNamespace

from src.course_api_service import CourseAPIService


class FakeCatalog:
    def __init__(self, cached=None):
        self.cached = cached
        self.cache_request = None
        self.saved = None

    def get_cached(self, provider, account_index, identity):
        self.cache_request = (provider, account_index, identity)
        return self.cached

    def put_cached(self, provider, account_index, identity, result):
        self.saved = (provider, account_index, identity, result)


class FakeProcess:
    def __init__(self, output=b"", returncode=0, *, read_error=None):
        self.stdout = io.BytesIO(output) if read_error is None else read_error
        self.returncode = returncode
        self.terminated = False

    def wait(self):
        return self.returncode

    def poll(self):
        return None if self.terminated is False else self.returncode


class BrokenOutput:
    def read(self, _size):
        raise OSError("pipe closed")


def make_launcher(root: Path, catalog: FakeCatalog):
    logs = []
    terminated = []
    launcher = SimpleNamespace(
        _load_autovisor_config_data=lambda: {
            "accounts": [{"account_id": 2, "username": "alice"}]
        },
        _load_yatori_config_data=lambda: {"users": []},
        _as_int=lambda value, default: int(value or default),
        _get_course_catalog_service=lambda: catalog,
        get_base_dir=lambda: str(root),
        get_python_executable=lambda: "python.exe",
        _python_module_available=lambda _python, _module: (True, ""),
        _get_subprocess_window_kwargs=lambda: (0, None),
        _build_encoding_candidates=lambda *_values: ("utf-8", "gbk"),
        _decode_output_line=lambda raw, encodings: raw.decode(encodings[0]),
        _clean_log_text=lambda value: value.strip(),
        _terminate_process_tree=lambda process, label: (
            setattr(process, "terminated", True),
            terminated.append(label),
        ),
        log_system=logs.append,
    )
    launcher.logs = logs
    launcher.terminated = terminated
    return launcher


def prepare_files(root: Path, initial_data: dict):
    scripts = root / "scripts"
    data = root / "data"
    scripts.mkdir()
    data.mkdir()
    (scripts / "fetch_zhs_courses.py").write_text("# fixture", encoding="utf-8")
    course_file = data / "zhs_course.json"
    course_file.write_text(
        json.dumps(initial_data, ensure_ascii=False),
        encoding="utf-8",
    )
    return course_file


def test_fresh_zhihuishu_result_is_parsed_cached_and_logs_unterminated_tail(tmp_path):
    course_file = prepare_files(root=tmp_path, initial_data={"alice": {"courses": []}})
    catalog = FakeCatalog()
    launcher = make_launcher(tmp_path, catalog)

    def create_process(*_args, **_kwargs):
        course_file.write_text(
            json.dumps(
                {
                    "alice": {
                        "courses": [
                            {
                                "courseName": "线性代数",
                                "secret": "course-secret",
                                "courseType": 1,
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return FakeProcess(b"fetch finished without newline")

    service = CourseAPIService(
        launcher,
        process_factory=create_process,
    )
    result = service.get_autovisor_courses(0)

    assert result["ok"] is True
    assert result["courses"][0]["name"] == "线性代数"
    assert catalog.cache_request == ("zhs", 1, "alice")
    assert catalog.saved[:3] == ("zhs", 1, "alice")
    assert any("fetch finished without newline" in line for line in launcher.logs)
    assert service._active_process is None


def test_concurrent_zhihuishu_fetch_is_rejected_before_launch(tmp_path):
    launcher = make_launcher(tmp_path, FakeCatalog())
    process_calls = []
    service = CourseAPIService(
        launcher,
        process_factory=lambda *_args, **_kwargs: process_calls.append(True),
    )
    service._fetch_lock.acquire()
    try:
        result = service.get_autovisor_courses(0)
    finally:
        service._fetch_lock.release()

    assert result == {
        "ok": False,
        "message": "智慧树课程获取正在进行中，请稍候",
    }
    assert process_calls == []


def test_stop_active_fetch_terminates_process_and_is_idempotent(tmp_path):
    launcher = make_launcher(tmp_path, FakeCatalog())
    service = CourseAPIService(launcher)
    process = FakeProcess()
    service._set_active_process(process)

    assert service.stop_active_fetch() is True
    assert launcher.terminated == ["课程获取"]
    assert service.stop_active_fetch() is False


def test_unchanged_course_content_is_rejected_even_if_script_succeeds(tmp_path):
    prepare_files(tmp_path, {"alice": {"courses": []}})
    launcher = make_launcher(tmp_path, FakeCatalog())

    result = CourseAPIService(
        launcher,
        process_factory=lambda *_args, **_kwargs: FakeProcess(),
    ).get_autovisor_courses(0)

    assert result["ok"] is False
    assert "未刷新数据文件" in result["message"]


def test_nonzero_fetch_exit_includes_output_tail_without_final_newline(tmp_path):
    prepare_files(tmp_path, {"alice": {"courses": []}})
    launcher = make_launcher(tmp_path, FakeCatalog())

    result = CourseAPIService(
        launcher,
        process_factory=lambda *_args, **_kwargs: FakeProcess(
            "登录验证失败".encode("utf-8"),
            returncode=4,
        ),
    ).get_autovisor_courses(0)

    assert result["ok"] is False
    assert "返回码:4" in result["message"]
    assert "登录验证失败" in result["message"]


def test_output_pipe_failure_terminates_orphan_fetch_process(tmp_path):
    prepare_files(tmp_path, {"alice": {"courses": []}})
    launcher = make_launcher(tmp_path, FakeCatalog())
    process = FakeProcess(read_error=BrokenOutput())

    service = CourseAPIService(
        launcher,
        process_factory=lambda *_args, **_kwargs: process,
    )
    result = service.get_autovisor_courses(0)

    assert result == {"ok": False, "message": "运行脚本失败: pipe closed"}
    assert launcher.terminated == ["课程获取"]
    assert service._active_process is None


def test_mismatched_course_identity_is_rejected_without_caching(tmp_path):
    course_file = prepare_files(tmp_path, {"alice": {"courses": []}})
    catalog = FakeCatalog()
    launcher = make_launcher(tmp_path, catalog)

    def create_process(*_args, **_kwargs):
        course_file.write_text(
            json.dumps({"bob": {"courses": []}}),
            encoding="utf-8",
        )
        return FakeProcess()

    result = CourseAPIService(
        launcher,
        process_factory=create_process,
    ).get_autovisor_courses(0)

    assert result["ok"] is False
    assert "没有账号 alice 的有效数据" in result["message"]
    assert catalog.saved is None


def test_playwright_probe_failure_returns_stable_web_error(tmp_path):
    prepare_files(tmp_path, {"alice": {"courses": []}})
    launcher = make_launcher(tmp_path, FakeCatalog())
    launcher._python_module_available = lambda *_args: (_ for _ in ()).throw(
        OSError("python unavailable")
    )

    result = CourseAPIService(launcher).get_autovisor_courses(0)

    assert result == {
        "ok": False,
        "message": "检查Playwright环境失败: python unavailable",
    }
