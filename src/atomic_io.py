"""Small atomic file-writing helpers for user configuration files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


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
