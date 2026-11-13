# -*- coding: utf-8 -*-
"""zhs 源码副本（zhs/）的校验、同步与重装。

本模块是 ZHS 核心升级的唯一实现，三个入口共用：

* scripts/update_zhs.py：维护者命令行，支持本地目录或 GitHub 归档。
* src/core_manager.py：启动器“检查更新 / 安装更新”。
* 测试：直接用临时目录构造新旧副本校验同步行为。

约定（与 zhs/UPSTREAM.json 一致）：

* zhs/ 是上游源码的零补丁副本，启动器侧集成代码不放在这里；
* 哈希与比较统一先做 CRLF -> LF 归一化，避免 Windows 检出与 GitHub 归档
  之间的换行符差异被误判为“所有文件都被修改”；
* 只有 SYNC_ENTRIES 里的顶层条目会被覆盖，UPSTREAM.json /
  UPSTREAM.sha256 / NOTICE.md 由本模块重写。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

# 只同步这些顶层条目（对应 zhs/pyproject.toml 的包布局与随附文档）
SYNC_ENTRIES: tuple[str, ...] = (
    "src",
    "pyproject.toml",
    "LICENSE",
    "CHANGELOG.md",
    "README.md",
    "README_zh.md",
    "config.toml.example",
    "uv.lock",
)
META_FILES = frozenset({"UPSTREAM.json", "UPSTREAM.sha256", "NOTICE.md"})
SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "tests",
        "docs",
        "scripts",
        ".github",
    }
)
SMOKE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("--version",),
    ("play", "--help"),
    ("fetch", "--help"),
    ("homework", "--help"),
    ("exam", "--help"),
)

UPSTREAM_JSON = "UPSTREAM.json"
UPSTREAM_SHA256 = "UPSTREAM.sha256"
REQUIREMENTS_NAME = "requirements-zhs.txt"
VENV_RELATIVE = Path(".runtime") / "venv-zhs" / "Scripts" / "python.exe"

# 与 src/install_sources.PYPI_MIRRORS 保持一致的兜底顺序；出现在这里是为了
# 让 zhs_vendor 能在不依赖启动器包结构的情况下独立运行（脚本/测试）。
PYPI_MIRROR_URLS: tuple[str, ...] = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://mirrors.huaweicloud.com/repository/pypi/simple",
    "https://pypi.org/simple",
)

_REQUIREMENTS_HEADER = (
    "# ZHS core (third core) runtime dependencies.",
    "# AUTO-GENERATED from zhs/pyproject.toml [project].dependencies by",
    "# src/zhs_vendor.py (scripts/update_zhs.py and the launcher updater).",
    "# Installed by bootstrap.py into .runtime/venv-zhs; requires Python >=3.13,<3.15.",
    "# NOTE: pip decodes requirement files with the system locale encoding on",
    "# Windows, so this file must stay pure ASCII (no CJK comments).",
)


class ZhsVendorError(RuntimeError):
    """zhs 副本无法安全同步（目录无效、本地有改动等）。"""


def _log_with(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def normalize_newlines(data: bytes) -> bytes:
    """把 CRLF 归一化为 LF，仅用于哈希与比较，不改动磁盘内容。"""
    return data.replace(b"\r\n", b"\n")


def sha256_file(path: Path) -> str:
    """按归一化换行后的内容计算 SHA-256。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(normalize_newlines(chunk))
    return digest.hexdigest()


def collect(root: Path, entries: Sequence[str] = SYNC_ENTRIES) -> dict[str, str]:
    """收集 相对路径 -> 归一化 SHA-256，只覆盖同步范围。"""
    root = Path(root)
    result: dict[str, str] = {}
    for entry in entries:
        target = root / entry
        if not target.exists():
            continue
        if target.is_file():
            result[entry] = sha256_file(target)
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def read_manifest(path: Path) -> dict[str, str]:
    """读取 sha256 与相对路径两列的清单文件。"""
    path = Path(path)
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        if rel:
            result[rel.strip()] = digest.strip().lower()
    return result


def write_manifest(target: Path, hashes: dict[str, str] | None = None) -> dict[str, str]:
    """按当前文件重写 UPSTREAM.sha256 并返回新的哈希表。"""
    target = Path(target)
    final = collect(target) if hashes is None else dict(hashes)
    manifest = "\n".join(f"{digest}  {name}" for name, digest in sorted(final.items()))
    (target / UPSTREAM_SHA256).write_text(manifest + "\n", encoding="utf-8")
    return final


def read_upstream_version(source: Path) -> str:
    """从 pyproject.toml 读取版本号。"""
    pyproject = Path(source) / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def read_local_meta(target: Path) -> dict:
    """读取 zhs/UPSTREAM.json；缺失或损坏时返回空字典。"""
    path = Path(target) / UPSTREAM_JSON
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_local_version(target: Path) -> str:
    version = read_local_meta(target).get("version", "")
    if version:
        return str(version)
    return read_upstream_version(target)


def read_git_rev(source: Path) -> str:
    """读取本地源码目录的 git 分支与提交（非 git 目录返回空串）。"""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=source, capture_output=True, text=True, timeout=10,
        )
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=source, capture_output=True, text=True, timeout=10,
        )
        if rev.returncode == 0 and rev.stdout.strip():
            return f"{branch.stdout.strip() or '?'}@{rev.stdout.strip()}"
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def check_drift(target: Path) -> tuple[list[str], list[str]]:
    """返回 (被改动的文件, 缺失的文件)，均相对 zhs/。"""
    target = Path(target)
    recorded = read_manifest(target / UPSTREAM_SHA256)
    current = collect(target)
    drift = sorted(
        name for name, digest in current.items()
        if recorded.get(name) not in (None, digest)
    )
    missing = sorted(name for name in recorded if name not in current)
    return drift, missing


def plan_sync(source: Path, target: Path) -> dict:
    """比较新版源码目录与本地副本，返回差异计划（不写盘）。"""
    source = Path(source)
    target = Path(target)
    if not (source / "pyproject.toml").is_file():
        raise ZhsVendorError(f"不是 zhs 仓库目录（缺少 pyproject.toml）：{source}")
    if not (source / "src" / "zhs").is_dir():
        raise ZhsVendorError(f"不是 zhs 仓库目录（缺少 src/zhs）：{source}")
    if not (target / "pyproject.toml").is_file():
        raise ZhsVendorError(f"未找到内置 zhs 副本：{target}")

    current = collect(target)
    incoming = collect(source)
    return {
        "old_version": read_local_version(target),
        "new_version": read_upstream_version(source),
        "current": current,
        "incoming": incoming,
        "changed": sorted(
            name for name in set(incoming) & set(current)
            if incoming[name] != current[name]
        ),
        "added": sorted(set(incoming) - set(current)),
        "removed": sorted(set(current) - set(incoming)),
        "schema_touched": sorted(
            name for name in set(incoming) & set(current)
            if name.endswith("config.py") and incoming[name] != current[name]
        ),
    }


def describe_plan(plan: dict, *, limit: int = 40) -> list[str]:
    """把差异计划格式化为日志行。"""
    lines = [
        "[比较] 上游 {} -> {}，共 {} 个文件".format(
            plan.get("old_version") or "?",
            plan.get("new_version") or "?",
            len(plan.get("incoming") or {}),
        )
    ]
    for label, key in (("修改", "changed"), ("新增", "added"), ("删除", "removed")):
        items = plan.get(key) or []
        if not items:
            continue
        lines.append("  {} {} 个：".format(label, len(items)))
        for name in items[:limit]:
            lines.append("    - {}".format(name))
        if len(items) > limit:
            lines.append("    ... 其余 {} 个省略".format(len(items) - limit))
    if plan.get("schema_touched"):
        lines.append("  [提醒] 配置模型有变化，请检查新字段是否需要暴露到 UI。")
    return lines


def _entry_is_ignored(relative: Path) -> bool:
    return any(part in SKIP_DIRS for part in relative.parts)


def sync_from_source(
    source: Path,
    target: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    git_rev: str | None = None,
    source_url: str = "",
    archive_sha256: str = "",
    record_source_path: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict:
    """用 source 覆盖 target 的同步范围，并重写哈希清单与元信息。

    dry_run 只返回计划，不写盘。本地副本被改动且未指定 force 时抛出
    ZhsVendorError，避免悄悄覆盖维护者的修改。
    """
    source = Path(source).resolve()
    target = Path(target).resolve()
    drift, missing = check_drift(target)
    plan = plan_sync(source, target)
    plan["drift"] = drift
    plan["missing"] = missing

    if (drift or missing) and not force and not dry_run:
        details = [f"  ~ {name}" for name in drift[:40]]
        details += [f"  ! 缺失 {name}" for name in missing[:40]]
        raise ZhsVendorError(
            "内置 zhs 副本与 UPSTREAM.sha256 不一致（本地有改动）：\n"
            + "\n".join(details)
            + "\n确认这些改动不需要保留后，再用 force 覆盖。"
        )

    for line in describe_plan(plan):
        _log_with(log, line)
    if dry_run:
        _log_with(log, "[dry-run] 未写入任何文件。")
        return plan

    # 覆盖同步范围内的文件
    for entry in SYNC_ENTRIES:
        src_entry = source / entry
        dst_entry = target / entry
        if src_entry.is_file():
            dst_entry.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_entry, dst_entry)
        elif src_entry.is_dir():
            if dst_entry.exists():
                shutil.rmtree(dst_entry)
            shutil.copytree(
                src_entry,
                dst_entry,
                ignore=shutil.ignore_patterns(*SKIP_DIRS),
            )

    # 清理上游已删除的文件（限定在同步范围内，绝不碰元信息文件）
    for name in plan["removed"]:
        if name in META_FILES:
            continue
        stale = target / name
        try:
            relative = stale.relative_to(target)
        except ValueError:
            continue
        if _entry_is_ignored(relative):
            continue
        if stale.is_file():
            stale.unlink()
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_dir() and path != target and not any(path.iterdir()):
            path.rmdir()

    final = write_manifest(target)
    plan["total"] = len(final)

    meta = read_local_meta(target)
    meta.update(
        {
            "version": plan["new_version"],
            "upstream_git": git_rev or read_git_rev(source) or meta.get("upstream_git", ""),
            "vendored_at": datetime.now(timezone.utc).isoformat(),
            "local_patches": [],
        }
    )
    if record_source_path:
        meta["upstream_source_path"] = str(source)
    if source_url:
        meta["upstream_source_url"] = source_url
    if archive_sha256:
        meta["upstream_archive_sha256"] = archive_sha256
    (target / UPSTREAM_JSON).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    dependencies = read_dependencies(target)
    if dependencies:
        requirements = write_requirements_file(
            requirements_path_for(target), dependencies
        )
        plan["requirements"] = str(requirements)
        _log_with(
            log,
            f"[依赖] 已按新版 pyproject 同步 {len(dependencies)} 项到 {requirements.name}",
        )
    _log_with(log, f"[完成] 已同步 {len(final)} 个文件，版本 {plan['new_version'] or '?'}")
    return plan


def read_dependencies(source: Path) -> list[str]:
    """读取 pyproject.toml 的 [project].dependencies（保持原有顺序）。"""
    pyproject = Path(source) / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    deps = project.get("dependencies")
    if not isinstance(deps, list):
        return []
    return [str(item).strip() for item in deps if str(item).strip()]


def requirements_path_for(target: Path) -> Path:
    """requirements-zhs.txt 位于 zhs/ 的同级（仓库根）。"""
    return Path(target).resolve().parent / REQUIREMENTS_NAME


def write_requirements_file(path: Path, dependencies: Sequence[str]) -> Path:
    """按上游依赖重写 requirements-zhs.txt（纯 ASCII 注释，防止 GBK 解码失败）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [*_REQUIREMENTS_HEADER]
    for dep in dependencies:
        text = str(dep).strip()
        if text:
            lines.append(text)
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return path


def ensure_requirements(
    root: Path,
    *,
    venv_python: Path | None = None,
    requirements_path: Path | None = None,
    mirrors: Sequence[str] = (),
    log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """在 venv-zhs 里安装 requirements-zhs.txt，逐镜像重试。"""
    root = Path(root)
    python = Path(venv_python) if venv_python else find_venv_python(root)
    if python is None or not python.is_file():
        return False, "未找到 .runtime/venv-zhs，无法安装 ZHS 依赖"
    target = Path(requirements_path) if requirements_path else requirements_path_for(root / "zhs")
    if not target.is_file():
        return False, f"未找到依赖清单：{target}"
    candidates = [str(url).strip() for url in mirrors if str(url).strip()] or list(PYPI_MIRROR_URLS)
    last_detail = ""
    for index_url in candidates:
        argv = [
            str(python), "-m", "pip", "install", "-r", str(target),
            "--disable-pip-version-check", "--prefer-binary", "--no-color", "--quiet",
            "-i", index_url,
        ]
        try:
            run = subprocess.run(
                argv, cwd=str(root), capture_output=True, text=True, timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            last_detail = f"{index_url} 无法执行：{exc}"
            _log_with(log, f"[依赖] {last_detail}")
            continue
        if run.returncode == 0:
            _log_with(log, f"[通过] ZHS 运行依赖已就绪（{index_url}）")
            return True, ""
        last_detail = f"{index_url} 退出码 {run.returncode}：{((run.stderr or run.stdout).strip()[-800:])}"
        _log_with(log, f"[依赖] {last_detail}")
    return False, f"ZHS 依赖安装失败：{last_detail}"


def find_venv_python(root: Path) -> Path | None:
    """返回 ZHS 独立解释器路径（不存在则 None）。"""
    candidate = Path(root) / VENV_RELATIVE
    return candidate if candidate.is_file() else None


def reinstall_editable(
    root: Path,
    *,
    venv_python: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """在 .runtime/venv-zhs 里重新安装 -e zhs --no-deps。"""
    root = Path(root)
    python = Path(venv_python) if venv_python else find_venv_python(root)
    if python is None or not python.is_file():
        return False, "未找到 .runtime/venv-zhs，无法重装 ZHS 包"
    try:
        run = subprocess.run(
            [
                str(python), "-m", "pip", "install", "-e", str(root / "zhs"),
                "--no-deps", "--disable-pip-version-check", "--no-color", "--quiet",
            ],
            cwd=str(root), capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"editable 重装失败：{exc}"
    if run.returncode != 0:
        detail = (run.stderr or run.stdout).strip()[-1500:]
        return False, f"editable 重装失败（退出码 {run.returncode}）：{detail}"
    _log_with(log, "[通过] zhs 包已重新安装")
    return True, ""


def smoke_check(
    root: Path,
    *,
    venv_python: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """运行 python -m zhs ... 冒烟命令，确认新版能正常启动。"""
    root = Path(root)
    python = Path(venv_python) if venv_python else find_venv_python(root)
    if python is None or not python.is_file():
        return False, "未找到 .runtime/venv-zhs，无法冒烟检查"
    for argv in SMOKE_COMMANDS:
        label = "zhs " + " ".join(argv)
        try:
            run = subprocess.run(
                [str(python), "-m", "zhs", *argv],
                cwd=str(root), capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"冒烟检查 {label} 无法执行：{exc}"
        if run.returncode != 0:
            detail = (run.stderr or run.stdout).strip()[-1200:]
            return False, f"冒烟检查 {label} 退出码 {run.returncode}：{detail}"
        _log_with(log, f"[通过] {label}")
    return True, ""
