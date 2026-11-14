"""Finish legacy four-path updates and clean only fully applied launcher ZIPs.

The builder puts the missing ZHS/bootstrap files inside src/, which old clients
already copy and verify. Never recover executable code from arbitrary leftovers.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from src.atomic_io import atomic_write_bytes, capture_file_state, restore_file_state

COMPAT_PAYLOAD = "src/legacy-zhs-payload.zip"
REQUIRED_COMPAT_FILES = {
    "bootstrap.py", "requirements-zhs.txt", "zhs/pyproject.toml",
    "zhs/UPSTREAM.json", "zhs/src/zhs/__init__.py",
}


def restore_legacy_layout(base_dir) -> bool:
    """Restore bundled code transactionally; preserve all account configuration."""
    root = Path(base_dir).resolve()
    archive = root / COMPAT_PAYLOAD
    if not archive.is_file():
        return False
    bootstrap = root / "bootstrap.py"
    if (root / "zhs/pyproject.toml").is_file() and (root / "requirements-zhs.txt").is_file():
        if bootstrap.is_file() and "def ensure_zhs_runtime(" in bootstrap.read_text(encoding="utf-8-sig"):
            return False

    updates = {}
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            name = info.filename
            parts = PurePosixPath(name).parts
            if (not parts or "\\" in name or PurePosixPath(name).is_absolute()
                    or any(part in ("..", ".") or ":" in part for part in parts)
                    or name not in ("bootstrap.py", "requirements-zhs.txt") and not name.startswith("zhs/")
                    or name == "zhs/config.toml" or name.startswith("zhs/.venv/")
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError(f"非法兼容文件路径: {name}")
            if info.is_dir():
                continue
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError(f"兼容文件越过安装目录: {name}")
            if target in updates:
                raise ValueError(f"重复兼容文件: {name}")
            updates[target] = package.read(info)
    if not REQUIRED_COMPAT_FILES.issubset(p.relative_to(root).as_posix() for p in updates):
        raise ValueError("旧版升级兼容包缺少 ZHS 必需文件")
    snapshot = capture_file_state(list(updates))
    try:
        for target, content in updates.items():
            atomic_write_bytes(target, content)
    except Exception:
        restore_file_state(snapshot)
        raise
    return True


def cleanup_applied_archives(base_dir, *, log=lambda message: None) -> list[str]:
    """Delete recognized ZIPs only when every payload file matches the install.

Both manually downloaded launcher-v*.zip and legacy .update-staging/v*.zip
are supported. Partial/old/full/core ZIPs and rollback backups stay untouched.
"""
    root = Path(base_dir).resolve()
    candidates = list(root.glob("launcher-v*.zip"))
    staging = root / ".update-staging"
    if staging.is_dir() and not staging.is_symlink():
        candidates.extend(staging.glob("v*.zip"))
    removed = []
    for archive in candidates:
        if archive.is_symlink() or not archive.resolve().is_relative_to(root):
            continue
        if not re.fullmatch(r"(?:launcher-)?v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\.zip", archive.name):
            continue
        try:
            with zipfile.ZipFile(archive) as package:
                names = {info.filename for info in package.infolist() if not info.is_dir()}
                if not {"统一启动器.py", "requirements.txt"}.issubset(names):
                    continue
                if not any(n.startswith("src/") for n in names) or not any(n.startswith("web/") for n in names):
                    continue
                matches = True
                for name in names:
                    parts = PurePosixPath(name).parts
                    target = root.joinpath(*parts)
                    if (not parts or "\\" in name or ".." in parts or ":" in name
                            or PurePosixPath(name).is_absolute()
                            or parts[0] not in {"统一启动器.py", "requirements.txt", "bootstrap.py", "requirements-zhs.txt", "src", "web", "zhs"}
                            or not target.resolve().is_relative_to(root) or not target.is_file()):
                        matches = False
                        break
                    info = package.getinfo(name)
                    if target.stat().st_size != info.file_size:
                        matches = False
                        break
                    with package.open(info) as source, target.open("rb") as installed:
                        while chunk := source.read(1024 * 1024):
                            if installed.read(len(chunk)) != chunk:
                                matches = False
                                break
                    if not matches:
                        break
            if matches:
                archive.unlink()
                removed.append(str(archive))
                log(f"[更新] 已清理应用完成的更新包: {archive.name}")
        except Exception as exc:
            log(f"[更新] 保留更新包 {archive.name}: {exc}")
    return removed


def finish_legacy_update(base_dir, *, log=lambda message: None) -> None:
    """Run before service composition, without networking or creating a venv."""
    try:
        if restore_legacy_layout(base_dir):
            log("[更新] 已补齐旧版更新遗漏的 bootstrap 与 ZHS 源码")
    except Exception as exc:
        log(f"[更新] 旧版升级文件补齐失败，保留更新包以便重试: {exc}")
        return
    cleanup_applied_archives(base_dir, log=log)
