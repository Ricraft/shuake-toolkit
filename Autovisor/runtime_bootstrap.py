"""Shared startup path handling for all Autovisor entry points."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DEPS = PROJECT_ROOT / "runtime_deps"


def activate_runtime_dependencies() -> Path:
    """Prepend bundled wheels before modules such as ``slider`` are imported."""
    root = str(PROJECT_ROOT)
    runtime = str(RUNTIME_DEPS)
    if root not in sys.path:
        sys.path.insert(0, root)
    if RUNTIME_DEPS.is_dir() and runtime not in sys.path:
        sys.path.insert(0, runtime)
    return RUNTIME_DEPS
