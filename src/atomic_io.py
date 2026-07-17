"""Small atomic file-writing helpers for user configuration files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Atomically replace *path* with binary *data*."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8") -> None:
    """Replace *path* only after the complete new content reaches disk."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def atomic_dump_json(
    path: str | os.PathLike[str],
    value: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=ensure_ascii, indent=indent) + "\n",
    )


def capture_file_state(
    paths: list[str | os.PathLike[str]] | tuple[str | os.PathLike[str], ...],
) -> dict[Path, bytes | None]:
    """Capture exact bytes and absence state for a small file transaction."""
    snapshot: dict[Path, bytes | None] = {}
    for path in paths:
        target = Path(path)
        snapshot[target] = target.read_bytes() if target.exists() else None
    return snapshot


def restore_file_state(snapshot: dict[Path, bytes | None]) -> None:
    """Restore every file in a snapshot, reporting all failed restores."""
    failures = []
    for target, content in snapshot.items():
        try:
            if content is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write_bytes(target, content)
        except Exception as exc:
            failures.append(f"{target}: {exc}")
    if failures:
        raise OSError("；".join(failures))
