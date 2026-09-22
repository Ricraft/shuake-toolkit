# encoding=utf-8
"""统一启动器自我更新的回归契约。

覆盖：语义版本比较、无 Release / 限流 / 网络失败的优雅返回、一次性确认令牌、
大小与 SHA-256 双重校验（Release 摘要优先、manifest 回退）、ZIP 越界拒绝、
替换白名单与保留清单、进程外应用脚本的引用转义与回滚，以及前后端接线契约。
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.launcher_update_service import (
    APPLY_SCRIPT_NAME,
    LAUNCHER_RELEASES_API_URL,
    LAUNCHER_REPO,
    PRESERVED_PATHS,
    REQUIRED_STAGED_FILES,
    REPLACE_WHITELIST,
    UPDATE_BACKUP_DIR_NAME,
    UPDATE_STAGING_DIR_NAME,
    LauncherUpdateService,
    build_apply_script,
    compare_launcher_versions,
    format_release_notes,
    normalize_sha256_digest,
    normalize_sha256_hex,
    safe_extract_zip,
    select_latest_release,
    write_apply_script,
)
from src.web_action_service import WebActionService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE = (PROJECT_ROOT / "web" / "现代启动器_UI_预览.html").read_text(encoding="utf-8")
APP_JS = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
LAUNCHER_SOURCE = (PROJECT_ROOT / "统一启动器.py").read_text(encoding="utf-8")
BUILDER_SOURCE = (
    PROJECT_ROOT / "scripts" / "build_launcher_release.py"
).read_text(encoding="utf-8")

TAG = "v1.3.0"
CURRENT = "v1.2.0"


@pytest.fixture()
def workspace():
    """本机 pytest 临时基目录不可访问，这里使用独立临时目录。"""
    path = tempfile.mkdtemp(prefix="dsh-launcher-update-")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------- 测试替身

def _release(tag=TAG, *, assets=None, draft=False, body="新增：统一启动器自更新",
             published_at="2026-09-01T00:00:00Z"):
    return {
        "tag_name": tag,
        "draft": draft,
        "published_at": published_at,
        "body": body,
        "html_url": f"https://github.com/{LAUNCHER_REPO}/releases/tag/{tag}",
        "assets": list(assets or []),
    }


def _zip_asset(tag=TAG, archive=None, *, with_digest=True, size=None):
    data = Path(archive).read_bytes() if archive else b""
    asset = {
        "name": f"launcher-{tag}.zip",
        "browser_download_url": f"https://example.invalid/launcher-{tag}.zip",
        "id": 11,
        "size": len(data) if size is None else size,
    }
    if with_digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return asset


def _manifest_asset_entry():
    return {
        "name": "launcher-manifest.json",
        "browser_download_url": "https://example.invalid/launcher-manifest.json",
        "id": 12,
        "size": 300,
    }


def make_service(base_dir, *, current_version=CURRENT, releases=None, manifest=None,
                 archive=None, status=200, opener_exc=None, **kwargs):
    logs = []
    calls = {"open": [], "download": []}

    def opener(url, timeout=None):
        calls["open"].append(url)
        if opener_exc is not None:
            raise opener_exc
        if url.endswith("launcher-manifest.json"):
            payload = manifest if manifest is not None else {}
            return 200, json.dumps(payload).encode("utf-8")
        return status, json.dumps(releases if releases is not None else []).encode("utf-8")

    def downloader(url, target_path, progress=None, timeout=None):
        calls["download"].append(url)
        if archive is None:
            return False
        shutil.copyfile(archive, target_path)
        if callable(progress):
            size = os.path.getsize(target_path)
            progress(size, size)
        return True

    service = LauncherUpdateService(
        str(base_dir),
        current_version=current_version,
        log=logs.append,
        opener=opener,
        downloader=downloader,
        pid=4242,
        **kwargs,
    )
    service.test_logs = logs
    service.test_calls = calls
    return service


def write_payload_zip(path, *, extra=None):
    entries = {
        "统一启动器.py": "# launcher\n",
        "requirements.txt": "# req\n",
        "src/__init__.py": "",
        "src/web_action_service.py": "# web actions\n",
        "src/launcher_update_service.py": "# update service\n",
        "web/app.js": "// app\n",
        "web/styles.css": "body{}\n",
        "web/现代启动器_UI_预览.html": "<html></html>\n",
    }
    if extra:
        entries.update(extra)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        for name, content in entries.items():
            handle.writestr(name, content)
    return Path(path)


def make_base_dir(workspace, name="统一 启动器 目录"):
    base = workspace / name
    (base / "src").mkdir(parents=True)
    (base / "web").mkdir()
    (base / "Yatori").mkdir()
    (base / "Autovisor").mkdir()
    (base / "data").mkdir()
    (base / "logs").mkdir()
    (base / "src" / "old_module.py").write_text("old\n", encoding="utf-8")
    (base / "统一启动器.py").write_text("# current launcher v1.2.0\n", encoding="utf-8")
    (base / "Yatori" / "config.yaml").write_text("account: secret\n", encoding="utf-8")
    (base / "data" / "cache.db").write_bytes(b"user-data")
    (base / "logs" / "run.log").write_text("log\n", encoding="utf-8")
    (base / "configs.ini").write_text("[default]\n", encoding="utf-8")
    return base


# --------------------------------------------------------------- 版本比较

def test_version_comparison_is_semantic():
    assert compare_launcher_versions("v1.3.0", "v1.2.0") == 1
    assert compare_launcher_versions("v1.2.0", "v1.3.0") == -1
    assert compare_launcher_versions("v1.2.0", "v1.2.0") == 0
    assert compare_launcher_versions("1.2.0", "v1.2") == 0  # 缺失段补零
    assert compare_launcher_versions("v1.2.1", "v1.2") == 1
    # beta11 与 beta.11 视为等价，且预发布低于正式版
    assert compare_launcher_versions("v1.3.0-beta11", "v1.3.0-beta.11") == 0
    assert compare_launcher_versions("v1.3.0-beta.11", "v1.3.0") == -1


def test_unparseable_versions_return_none():
    assert compare_launcher_versions("nightly", "v1.2.0") is None
    assert compare_launcher_versions("v1.2.0", "latest") is None


def test_normalize_sha256_helpers():
    digest = "a" * 64
    assert normalize_sha256_digest(f"sha256:{digest.upper()}") == digest
    assert normalize_sha256_digest(f"sha512:{digest}") is None
    assert normalize_sha256_digest(digest) is None
    assert normalize_sha256_digest("sha256:abc") is None
    assert normalize_sha256_hex(digest) == digest
    assert normalize_sha256_hex("not-a-digest") is None


def test_select_latest_release_skips_drafts_and_unparseable_tags():
    releases = [
        _release("nightly"),
        _release("v9.9.9", draft=True),
        _release("v1.3.0", published_at="2026-01-01T00:00:00Z"),
        _release("v1.4.0", published_at="2026-02-01T00:00:00Z"),
    ]
    selected = select_latest_release(releases)
    assert selected["tag_name"] == "v1.4.0"


def test_release_notes_are_condensed():
    body = "<!-- 内部注释 -->\n\n第一行\n第二行\n"
    assert format_release_notes(body) == "第一行\n第二行"
    assert format_release_notes("") == ""
    assert len(format_release_notes("x" * 2000, max_chars=100)) == 103


# --------------------------------------------------------------- 检查更新

def test_no_release_is_a_graceful_path(workspace):
    service = make_service(workspace, releases=[])

    result = service.prepare_update_confirmation()

    assert result["ok"] is True
    assert result["noRelease"] is True
    assert "暂无可用版本" in result["message"]
    assert "updateDialog" not in result
    assert not any("Traceback" in line for line in service.test_logs)
    assert service.test_calls["download"] == []
    assert service.test_calls["open"] == [LAUNCHER_RELEASES_API_URL]


def test_unparseable_release_tags_are_treated_as_no_release(workspace):
    service = make_service(workspace, releases=[_release("nightly-build")])

    result = service.prepare_update_confirmation()

    assert result["ok"] is True
    assert result["noRelease"] is True
    assert "暂无可用版本" in result["message"]


def test_rate_limit_and_network_errors_are_readable(workspace):
    limited = make_service(workspace, status=403)
    limited_result = limited.prepare_update_confirmation()
    assert limited_result["ok"] is False
    assert "403" in limited_result["message"]
    assert "updateDialog" not in limited_result

    broken = make_service(workspace, opener_exc=OSError("网络不可达"))
    broken_result = broken.prepare_update_confirmation()
    assert broken_result["ok"] is False
    assert "网络不可达" in broken_result["message"]

    missing = make_service(workspace, status=404)
    assert "404" in missing.prepare_update_confirmation()["message"]


def test_remote_not_newer_never_offers_update(workspace):
    same = make_service(workspace, releases=[_release(CURRENT)])
    same_result = same.prepare_update_confirmation()
    assert same_result["ok"] is True
    assert same_result["upToDate"] is True
    assert "已是最新版本" in same_result["message"]
    assert "updateDialog" not in same_result

    older = make_service(workspace, releases=[_release("v1.1.0")])
    older_result = older.prepare_update_confirmation()
    assert older_result["ok"] is True
    assert older_result["localNewer"] is True
    assert "高于远端" in older_result["message"]
    assert "updateDialog" not in older_result

    highest = make_service(
        workspace,
        releases=[
            _release("v1.2.5", published_at="2026-05-01T00:00:00Z"),
            _release("v1.4.0", published_at="2026-01-01T00:00:00Z"),
        ],
    )
    assert highest.check_for_update()["latestVersion"] == "v1.4.0"


def test_update_available_builds_dialog_with_required_fields(workspace):
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(workspace, releases=[release], archive=str(archive))

    result = service.prepare_update_confirmation()

    assert result["ok"] is True
    dialog = result["updateDialog"]
    for field in (
        "currentVersion",
        "latestVersion",
        "assetName",
        "publishedAt",
        "releaseNotes",
        "confirmationToken",
    ):
        assert dialog[field], field
    assert dialog["currentVersion"] == CURRENT
    assert dialog["latestVersion"] == TAG
    assert dialog["assetName"] == f"launcher-{TAG}.zip"
    assert dialog["publishedAt"] == "2026-09-01T00:00:00Z"
    assert dialog["releaseNotes"] == "新增：统一启动器自更新"
    assert dialog["downloadable"] is True
    assert dialog["core"] == "launcher"
    assert service.test_calls["download"] == []


def test_install_is_rejected_while_another_install_runs(workspace):
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(workspace, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    blocked = []
    service.installing = True
    assert service.install_confirmed(token)["ok"] is False
    assert service.prepare_update_confirmation()["ok"] is False
    assert blocked == []
    service.installing = False
    assert service.install_confirmed(token)["ok"] is True


# --------------------------------------------------------------- 一次性令牌

def test_missing_or_wrong_token_is_rejected_without_downloading(workspace):
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(workspace, releases=[release], archive=str(archive))
    service.prepare_update_confirmation()

    for token in (None, "", "not-the-token"):
        result = service.install_confirmed(token)
        assert result["ok"] is False
        assert "确认已失效" in result["message"]
    assert service.test_calls["download"] == []


def test_token_is_one_shot_and_replay_is_rejected(workspace):
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(workspace, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    first = service.install_confirmed(token)
    replay = service.install_confirmed(token)

    assert first["ok"] is True
    assert replay["ok"] is False
    assert "确认已失效" in replay["message"]
    assert len(service.test_calls["download"]) == 1


def test_checking_again_invalidates_the_previous_token(workspace):
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(workspace, releases=[release], archive=str(archive))
    stale = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]
    fresh = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    assert service.install_confirmed(stale)["ok"] is False
    assert service.install_confirmed(fresh)["ok"] is True
    assert len(service.test_calls["download"]) == 1


# --------------------------------------------------------------- 下载与校验

def test_install_verifies_asset_digest_and_stages_without_touching_files(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is True, result
    assert result["staged"] is True
    assert result["scriptName"] == APPLY_SCRIPT_NAME
    assert "退出启动器后运行" in result["message"]

    staging = base / UPDATE_STAGING_DIR_NAME
    assert (staging / TAG / "统一启动器.py").is_file()
    assert (staging / TAG / "src" / "web_action_service.py").is_file()
    assert (staging / TAG / "web" / "app.js").is_file()
    assert (staging / f"{TAG}.zip").is_file()
    assert (staging / APPLY_SCRIPT_NAME).is_file()

    # 运行中的文件与用户数据从未被覆盖
    assert (base / "统一启动器.py").read_text(encoding="utf-8") == "# current launcher v1.2.0\n"
    assert (base / "src" / "old_module.py").read_text(encoding="utf-8") == "old\n"
    assert (base / "Yatori" / "config.yaml").read_text(encoding="utf-8") == "account: secret\n"
    assert (base / "data" / "cache.db").read_bytes() == b"user-data"
    assert (base / "logs" / "run.log").read_text(encoding="utf-8") == "log\n"
    assert (base / "configs.ini").read_text(encoding="utf-8") == "[default]\n"
    assert sorted(path.name for path in base.iterdir()) == [
        UPDATE_STAGING_DIR_NAME,
        "Autovisor",
        "Yatori",
        "configs.ini",
        "data",
        "logs",
        "src",
        "web",
        "统一启动器.py",
    ]


def test_manifest_fallback_supplies_size_and_sha256(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    data = archive.read_bytes()
    asset = {
        "name": f"launcher-{TAG}.zip",
        "browser_download_url": f"https://example.invalid/launcher-{TAG}.zip",
        "id": 11,
    }
    release = _release(TAG, assets=[asset, _manifest_asset_entry()])
    manifest = {
        "version": TAG,
        "asset": f"launcher-{TAG}.zip",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    service = make_service(
        base, releases=[release], archive=str(archive), manifest=manifest
    )
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is True, result
    assert any(
        "launcher-manifest.json" in line for line in service.test_logs
    )


def test_manifest_version_mismatch_is_refused(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    data = archive.read_bytes()
    asset = {
        "name": f"launcher-{TAG}.zip",
        "browser_download_url": "https://example.invalid/zip",
        "id": 11,
    }
    release = _release(TAG, assets=[asset, _manifest_asset_entry()])
    manifest = {
        "version": "v9.9.9",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    service = make_service(
        base, releases=[release], archive=str(archive), manifest=manifest
    )
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "不一致" in result["message"]


def test_size_mismatch_is_refused(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(
        TAG,
        assets=[
            _zip_asset(TAG, archive, size=len(archive.read_bytes()) + 1),
            _manifest_asset_entry(),
        ],
    )
    service = make_service(base, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "大小不匹配" in result["message"]
    assert not (base / UPDATE_STAGING_DIR_NAME / TAG / "统一启动器.py").exists()


def test_sha256_mismatch_is_refused(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    asset = _zip_asset(TAG, archive)
    asset["digest"] = "sha256:" + "b" * 64
    release = _release(TAG, assets=[asset, _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "SHA-256" in result["message"]


def test_release_without_any_verification_source_is_refused_before_download(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    asset = {
        "name": f"launcher-{TAG}.zip",
        "browser_download_url": "https://example.invalid/zip",
        "id": 11,
        "size": 100,
    }
    release = _release(TAG, assets=[asset])
    service = make_service(base, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "拒绝自动安装" in result["message"]
    assert service.test_calls["download"] == []


def test_release_without_launcher_asset_is_refused(workspace):
    base = make_base_dir(workspace)
    release = _release(TAG, assets=[_manifest_asset_entry()])
    service = make_service(base, releases=[release])
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    assert token
    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "未提供可下载的更新资源" in result["message"]
    assert service.test_calls["download"] == []


def test_download_failure_is_reported(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=None)
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "下载失败" in result["message"]


def test_zip_path_traversal_is_rejected(workspace):
    base = make_base_dir(workspace)
    bad = workspace / "bad.zip"
    with zipfile.ZipFile(bad, "w") as handle:
        handle.writestr("统一启动器.py", "# x\n")
        handle.writestr("src/web_action_service.py", "# x\n")
        handle.writestr("../escaped.txt", "bad")
    release = _release(TAG, assets=[_zip_asset(TAG, bad), _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=str(bad))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "越界" in result["message"]
    assert not (base / "escaped.txt").exists()
    assert not (workspace / "escaped.txt").exists()

    with zipfile.ZipFile(bad) as handle:
        with pytest.raises(ValueError):
            safe_extract_zip(handle, workspace / "extract")


def test_missing_staged_files_are_reported(workspace):
    base = make_base_dir(workspace)
    partial = workspace / "partial.zip"
    with zipfile.ZipFile(partial, "w") as handle:
        handle.writestr("统一启动器.py", "# launcher\n")
    release = _release(TAG, assets=[_zip_asset(TAG, partial), _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=str(partial))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "缺少必要文件" in result["message"]
    assert "src" in result["message"] and "web" in result["message"]


def test_async_install_reports_result_through_schedule(workspace):
    base = make_base_dir(workspace)
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    results = []
    service = make_service(
        base,
        releases=[release],
        archive=str(archive),
        on_result=results.append,
    )
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]
    scheduled = []
    finished = threading.Event()

    def schedule(delay, callback):
        scheduled.append(delay)
        callback()
        finished.set()

    accepted = service.install_confirmed_async(
        token, run_async=lambda target: target(), schedule=schedule
    )

    assert accepted is True
    assert finished.is_set()
    assert scheduled == [0]
    assert service.installing is False
    assert results and results[0]["ok"] is True
    assert "退出启动器后运行" in results[0]["message"]


def test_async_install_rejects_unusable_token(workspace):
    base = make_base_dir(workspace)
    service = make_service(base, releases=[])

    assert service.install_confirmed_async("nope", run_async=lambda target: target()) is False
    assert service.installing is False


# --------------------------------------------------------------- 替换白名单与脚本

def test_replace_whitelist_matches_release_builder_contract():
    assert REPLACE_WHITELIST == ("统一启动器.py", "requirements.txt", "src", "web")
    assert PRESERVED_PATHS[:4] == ("Yatori", "Autovisor", "data", "logs")
    for entry in REPLACE_WHITELIST:
        assert entry in BUILDER_SOURCE, entry
    assert "replaceWhitelist" in BUILDER_SOURCE
    assert LAUNCHER_REPO == "Ricraft/shuake-toolkit"
    assert LAUNCHER_RELEASES_API_URL == (
        "https://api.github.com/repos/Ricraft/shuake-toolkit/releases?per_page=20"
    )


def test_apply_script_only_replaces_whitelist_and_quotes_every_path():
    script = build_apply_script(
        version=TAG, current_version=CURRENT, launcher_pid=4321
    )

    assert "\r\n" in script
    assert script.startswith("@echo off\r\n")
    assert "chcp 65001 >nul" in script
    assert 'for %%I in ("%~dp0..") do set "ROOT=%%~fI"' in script
    assert 'set "LAUNCHER_PID=4321"' in script
    assert f'set "PAYLOAD=%~dp0{TAG}"' in script

    for expected in (
        'robocopy "%PAYLOAD%\\src" "%ROOT%\\src"',
        'robocopy "%PAYLOAD%\\web" "%ROOT%\\web"',
        'copy /Y "%PAYLOAD%\\统一启动器.py" "%ROOT%\\统一启动器.py"',
        'copy /Y "%PAYLOAD%\\requirements.txt" "%ROOT%\\requirements.txt"',
        'robocopy "%ROOT%\\src" "%BACKUP%\\src"',
        'robocopy "%ROOT%\\web" "%BACKUP%\\web"',
        'copy /Y "%ROOT%\\统一启动器.py" "%BACKUP%\\统一启动器.py"',
        'robocopy "%BACKUP%\\src" "%ROOT%\\src" /MIR',
        'robocopy "%BACKUP%\\web" "%ROOT%\\web" /MIR',
        'if "%FAILED%"=="1" goto rollback',
        'if not exist "%ROOT%\\src\\web_action_service.py" set "FAILED=1"',
        "tasklist /FI",
        "timeout /t 1 /nobreak",
        "exit /b 1",
        "exit /b 0",
    ):
        assert expected in script, expected

    for forbidden in (
        "%ROOT%\\Yatori",
        "%ROOT%\\Autovisor",
        "%ROOT%\\data",
        "%ROOT%\\logs",
        "configs.ini",
        "qb_config.json",
        "launcher_preferences.json",
        "%ROOT%\\.update-backup\\..\\..",
    ):
        assert forbidden not in script, forbidden

    for line in script.splitlines():
        stripped = line.strip()
        if "robocopy " not in stripped and not stripped.startswith("copy "):
            continue
        if "rem " in stripped:
            continue
        assert stripped.count('"') >= 4, stripped


def test_apply_script_round_trips_unicode_and_space_paths(workspace):
    target = workspace / "带 空格 与全角（测试）" / ".update-staging" / APPLY_SCRIPT_NAME
    text = build_apply_script(version=TAG, current_version=CURRENT, launcher_pid=1)

    write_apply_script(str(target), text)

    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    with open(target, encoding="utf-8-sig", newline="") as handle:
        assert handle.read() == text
    restored = target.read_text(encoding="utf-8-sig")
    assert "%~dp0" in restored
    assert "统一启动器.py" in restored


def test_apply_script_waits_for_the_launcher_pid():
    script = build_apply_script(version=TAG, current_version=CURRENT, launcher_pid=99)
    assert 'set "LAUNCHER_PID=99"' in script
    assert ":wait_launcher" in script
    assert 'if "%LAUNCHER_PID%"=="0" goto ready' in script
    skipped = build_apply_script(
        version=TAG, current_version=CURRENT, launcher_pid=0
    )
    assert 'set "LAUNCHER_PID=0"' in skipped


# --------------------------------------------------------------- 前后端接线

def make_launcher(**overrides):
    values = {
        "get_web_initial_state": lambda: {"runtime": {"yatori": False}},
        "_show_error": lambda title, message: None,
        "show_launcher_update_dialog": lambda: {
            "ok": True,
            "updateDialog": {
                "latestVersion": TAG,
                "currentVersion": CURRENT,
                "confirmationToken": "confirm-token",
            },
        },
        "install_launcher_update_async": lambda token: token == "confirm-token",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_web_action_dispatch_for_launcher_update():
    service = WebActionService(make_launcher())

    checked = service.perform("check_launcher_update")
    assert checked["ok"] is True
    assert checked["updateDialog"]["latestVersion"] == TAG
    assert checked["state"] == {"runtime": {"yatori": False}}

    installed = service.perform("install_launcher_update", "confirm-token")
    assert installed["ok"] is True

    rejected = service.perform("install_launcher_update", "stale-token")
    assert rejected["ok"] is False
    assert "更新确认已失效" in rejected["message"]


def test_web_action_dispatch_reports_check_failure():
    launcher = make_launcher(
        show_launcher_update_dialog=lambda: {"ok": False, "message": "GitHub 访问被拒绝"}
    )

    result = WebActionService(launcher).perform("check_launcher_update")

    assert result["ok"] is False
    assert result["message"] == "GitHub 访问被拒绝"


def test_launcher_forwards_launcher_update_actions():
    from 统一启动器 import UnifiedLauncher

    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    calls = []
    dialog = {
        "ok": True,
        "updateDialog": {"latestVersion": TAG, "confirmationToken": "tok"},
    }
    launcher._launcher_update_service = SimpleNamespace(
        prepare_update_confirmation=lambda: dialog,
        install_confirmed_async=lambda token, **kwargs: calls.append(
            (token, sorted(kwargs))
        )
        or True,
    )

    assert launcher.show_launcher_update_dialog() == dialog
    assert launcher.install_launcher_update_async("tok") is True
    assert calls == [("tok", ["schedule"])]
    assert "current_version=self.LAUNCHER_VERSION" in LAUNCHER_SOURCE
    assert "from src.launcher_update_service import LauncherUpdateService" in LAUNCHER_SOURCE


def test_about_page_exposes_launcher_update_button_and_modal():
    assert "检查统一启动器更新" in PAGE
    assert "onclick=\"openLauncherUpdateDialog()\"" in PAGE
    assert 'id="launcher-update-modal"' in PAGE
    assert 'id="launcher-update-confirm-btn"' in PAGE
    assert "class=\"modal-box update-modal-box\"" in PAGE
    assert "class=\"update-version-grid\"" in PAGE
    assert PAGE.count("update-notes-title") >= 2
    assert "稍后再说" in PAGE

    # 既有 Yatori / Autovisor 契约与按钮全部保留
    assert 'id="yatori-update-modal"' in PAGE
    assert "openYatoriUpdateDialog()" in PAGE
    assert "安装 Yatori 更新" in PAGE
    assert "检查 Autovisor 更新" in PAGE
    assert "Autovisor 本地适配版" in PAGE
    assert "我的成就" in PAGE
    assert "最新版本介绍" in PAGE
    assert "确认更新" in PAGE
    assert PAGE.count("disabled title=") >= 2
    assert "performAction('install_autovisor_update')" not in PAGE


def test_frontend_launcher_update_flow_uses_one_shot_token():
    assert "async function openLauncherUpdateDialog" in APP_JS
    assert "function showLauncherUpdateModal" in APP_JS
    assert "function closeLauncherUpdateModal" in APP_JS
    assert "async function confirmLauncherUpdate" in APP_JS
    assert "'check_launcher_update'" in APP_JS
    assert "'install_launcher_update'," in APP_JS
    assert "launcher-update-modal" in APP_JS
    assert "pendingLauncherUpdateDialog" in APP_JS

    open_source = APP_JS.split("async function openLauncherUpdateDialog", 1)[1].split(
        "function showLauncherUpdateModal", 1
    )[0]
    assert "runWebActionLocked('check_launcher_update', []" in open_source
    assert "apiCall('perform_action', 'check_launcher_update')" in open_source
    assert "install_launcher_update" not in open_source

    close_source = APP_JS.split("function closeLauncherUpdateModal", 1)[1].split(
        "async function confirmLauncherUpdate", 1
    )[0]
    assert "classList.remove('active')" in close_source
    assert "pendingLauncherUpdateDialog = null" in close_source
    assert "perform_action" not in close_source

    confirm_source = APP_JS.split("async function confirmLauncherUpdate", 1)[1]
    after_action = confirm_source.split("'install_launcher_update',", 1)[1]
    assert "confirmationToken" in after_action
    assert "pendingLauncherUpdateDialog = null" in confirm_source


def test_launcher_update_functions_are_appended_after_existing_slices():
    order = [
        "async function performAction",
        "async function openYatoriUpdateDialog",
        "function setText",
        "function showYatoriUpdateModal",
        "function closeYatoriUpdateModal",
        "async function confirmYatoriUpdate",
        "async function confirmAndPerform",
        "async function openLauncherUpdateDialog",
        "async function confirmLauncherUpdate",
    ]
    positions = [APP_JS.index(name) for name in order]
    assert positions == sorted(positions)
    assert APP_JS.rstrip().endswith("}")


def test_space_and_unicode_paths_are_used_in_service_paths(workspace):
    """本仓库路径含空格与全角字符，暂存与备份目录必须能正常创建。"""
    base = workspace / "刷课工具包_完整版( •̀ ω •́ )↗　"
    base.mkdir()
    archive = write_payload_zip(workspace / "launcher-v1.3.0.zip")
    release = _release(TAG, assets=[_zip_asset(TAG, archive), _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=str(archive))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is True, result
    script = Path(result["scriptPath"]).read_text(encoding="utf-8-sig")
    assert os.path.basename(result["scriptPath"]) == APPLY_SCRIPT_NAME
    # 脚本不硬编码绝对路径：从 %~dp0.. 推导，移动整个目录后仍然可用
    assert "%~dp0.." in script
    assert str(base) not in script
    assert script.count('"%ROOT%') >= 8

# --------------------------------------------------------------- 脚本真实执行

E2E_HTML = "现代启动器_UI_预览.html"


def _make_e2e_root(base, *, complete_payload=True):
    root = Path(base) / "刷课工具包_完整版( 测试 版 )"
    (root / "src").mkdir(parents=True)
    (root / "web").mkdir()
    for name in ("Yatori", "data", "logs"):
        (root / name).mkdir()
    (root / "统一启动器.py").write_text("OLD-LAUNCHER", encoding="utf-8")
    (root / "requirements.txt").write_text("OLD-REQ", encoding="utf-8")
    (root / "src" / "keep.py").write_text("OLD-SRC", encoding="utf-8")
    (root / "web" / "app.js").write_text("OLD-APP", encoding="utf-8")
    (root / "web" / "styles.css").write_text("OLD-CSS", encoding="utf-8")
    (root / "web" / E2E_HTML).write_text("OLD-HTML", encoding="utf-8")
    (root / "Yatori" / "config.yaml").write_text("KEEP-YATORI", encoding="utf-8")
    (root / "data" / "cache.db").write_text("KEEP-DATA", encoding="utf-8")
    (root / "logs" / "run.log").write_text("KEEP-LOG", encoding="utf-8")
    (root / "configs.ini").write_text("KEEP-INI", encoding="utf-8")

    payload = root / UPDATE_STAGING_DIR_NAME / TAG
    (payload / "src").mkdir(parents=True)
    (payload / "web").mkdir()
    (payload / "统一启动器.py").write_text("NEW-LAUNCHER", encoding="utf-8")
    (payload / "requirements.txt").write_text("NEW-REQ", encoding="utf-8")
    (payload / "src" / "module.py").write_text("NEW-SRC", encoding="utf-8")
    if complete_payload:
        (payload / "src" / "web_action_service.py").write_text("NEW-WAS", encoding="utf-8")
    (payload / "web" / "app.js").write_text("NEW-APP", encoding="utf-8")
    (payload / "web" / "styles.css").write_text("NEW-CSS", encoding="utf-8")
    (payload / "web" / E2E_HTML).write_text("NEW-HTML", encoding="utf-8")

    script = root / UPDATE_STAGING_DIR_NAME / APPLY_SCRIPT_NAME
    write_apply_script(
        str(script),
        build_apply_script(version=TAG, current_version=CURRENT, launcher_pid=0),
    )
    return root, script


def _run_apply_script(script):
    import subprocess

    return subprocess.run(
        f'cmd /c ""{script}"" < nul',
        shell=True,
        capture_output=True,
        timeout=120,
    )


@pytest.mark.skipif(os.name != "nt", reason="应用脚本是 Windows 批处理")
def test_apply_script_runs_on_a_space_and_cjk_path(workspace):
    root, script = _make_e2e_root(workspace)

    completed = _run_apply_script(script)

    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
    assert (root / "统一启动器.py").read_text(encoding="utf-8") == "NEW-LAUNCHER"
    assert (root / "requirements.txt").read_text(encoding="utf-8") == "NEW-REQ"
    assert (root / "src" / "module.py").read_text(encoding="utf-8") == "NEW-SRC"
    assert (root / "src" / "web_action_service.py").read_text(encoding="utf-8") == "NEW-WAS"
    # 覆盖式替换：更新包里没有的旧文件不会被删除
    assert (root / "src" / "keep.py").read_text(encoding="utf-8") == "OLD-SRC"
    assert (root / "web" / "app.js").read_text(encoding="utf-8") == "NEW-APP"
    assert (root / "web" / E2E_HTML).read_text(encoding="utf-8") == "NEW-HTML"
    # 第三方核心、用户数据与配置文件原样保留
    assert (root / "Yatori" / "config.yaml").read_text(encoding="utf-8") == "KEEP-YATORI"
    assert (root / "data" / "cache.db").read_text(encoding="utf-8") == "KEEP-DATA"
    assert (root / "logs" / "run.log").read_text(encoding="utf-8") == "KEEP-LOG"
    assert (root / "configs.ini").read_text(encoding="utf-8") == "KEEP-INI"
    # 备份保留更新前版本
    backups = list((root / UPDATE_BACKUP_DIR_NAME).glob("*"))
    assert backups, "备份目录未生成"
    assert (backups[0] / "统一启动器.py").read_text(encoding="utf-8") == "OLD-LAUNCHER"
    assert (backups[0] / "src" / "keep.py").read_text(encoding="utf-8") == "OLD-SRC"


@pytest.mark.skipif(os.name != "nt", reason="应用脚本是 Windows 批处理")
def test_apply_script_rolls_back_when_a_replaced_file_is_missing(workspace):
    root, script = _make_e2e_root(workspace, complete_payload=False)

    completed = _run_apply_script(script)

    assert completed.returncode == 1
    assert not (root / "src" / "module.py").exists()
    assert (root / "src" / "keep.py").read_text(encoding="utf-8") == "OLD-SRC"
    assert (root / "统一启动器.py").read_text(encoding="utf-8") == "OLD-LAUNCHER"
    assert (root / "requirements.txt").read_text(encoding="utf-8") == "OLD-REQ"

# ------------------------------------------------- 发布完整性（独立验证 F1 回归）


def test_required_staged_files_include_the_launcher_page():
    normalized = {item.replace(os.sep, "/") for item in REQUIRED_STAGED_FILES}
    assert f"web/{"现代启动器_UI_预览.html"}" in normalized, normalized


def test_package_missing_launcher_page_is_rejected(workspace):
    """残缺包（缺首屏入口页）必须在替换前被拒绝，不能退化成新旧混合安装。"""
    base = make_base_dir(workspace)
    partial = workspace / "no-page.zip"
    entries = {
        "统一启动器.py": "# launcher\n",
        "requirements.txt": "# req\n",
        "src/web_action_service.py": "# actions\n",
        "web/app.js": "// app\n",
    }
    with zipfile.ZipFile(partial, "w") as handle:
        for name, content in entries.items():
            handle.writestr(name, content)
    release = _release(TAG, assets=[_zip_asset(TAG, partial), _manifest_asset_entry()])
    service = make_service(base, releases=[release], archive=str(partial))
    token = service.prepare_update_confirmation()["updateDialog"]["confirmationToken"]

    result = service.install_confirmed(token)

    assert result["ok"] is False
    assert "缺少必要文件" in result["message"]
    assert "现代启动器_UI_预览.html" in result["message"]
    # 服务是在解压后校验必需文件的，所以这里断言关键事实：没有生成应用脚本，
    # 用户不可能误用这个残缺包去替换现有安装。
    assert not (base / UPDATE_STAGING_DIR_NAME / APPLY_SCRIPT_NAME).exists()



def test_apply_script_compares_content_instead_of_only_existence():
    """只判断存在会让残缺包静默留下旧文件；脚本必须逐字节比对关键文件。"""
    script = build_apply_script(version=TAG, current_version=CURRENT, launcher_pid=0)

    assert "fc /b" in script
    for relative in (
        "统一启动器.py",
        "src\\web_action_service.py",
        "web\\app.js",
        f"web\\{"现代启动器_UI_预览.html"}",
    ):
        expected = f'"%PAYLOAD%\\{relative}" "%ROOT%\\{relative}"'
        assert f"fc /b {expected}" in script, relative
