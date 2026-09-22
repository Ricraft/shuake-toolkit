# encoding=utf-8
"""Build the unified launcher release assets.

Produces, into the output directory:

* ``launcher-<version>.zip``   -- the files a launcher self-update is allowed to replace
* ``launcher-manifest.json``   -- version / asset name / byte size / sha256

The zip intentionally contains only the launcher's own source tree. Third-party
cores (``Yatori/``), the locally patched core (``Autovisor/``), user data
(``data/``, ``logs/``) and the bootstrapper ``启动器.exe`` are never packaged:
a self-update replaces files from a whitelist, and shipping anything else would
risk clobbering user data or an executable that is locked while running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Paths (relative to the project root) that are part of a launcher update.
WHITELIST = (
    "统一启动器.py",
    "requirements.txt",
    "src",
    "web",
)

# Never packaged, even when they live under a whitelisted directory.
EXCLUDED_DIR_NAMES = {"__pycache__", ".pytest_cache", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")

# 更新包必须包含这些入口文件：缺任一项都说明打包不完整，直接失败而不是产出坏包。
REQUIRED_FILES = (
    "统一启动器.py",
    "requirements.txt",
    "src/web_action_service.py",
    "web/app.js",
    "web/现代启动器_UI_预览.html",
)
LAUNCHER_VERSION_RE = re.compile(r'LAUNCHER_VERSION\s*=\s*"([^"]+)"')


def normalize_version(raw: str) -> str:
    """Return the canonical ``vX.Y.Z`` form, rejecting anything unparseable."""
    version = str(raw or "").strip()
    if not version:
        raise ValueError("version is empty")
    if not VERSION_RE.match(version):
        raise ValueError(f"version {version!r} is not a vX.Y.Z style version")
    return version if version.startswith("v") else f"v{version}"


def declared_launcher_version() -> str:
    """Read ``LAUNCHER_VERSION`` out of the launcher entry point."""
    entry = PROJECT_ROOT / "统一启动器.py"
    match = LAUNCHER_VERSION_RE.search(entry.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError("LAUNCHER_VERSION not found in 统一启动器.py")
    return normalize_version(match.group(1))


def iter_payload(root: Path):
    """Yield the files that belong in a launcher update, as (path, arcname)."""
    missing = [entry for entry in WHITELIST if not (root / entry).exists()]
    if missing:
        raise FileNotFoundError(f"whitelisted path(s) missing: {', '.join(missing)}")

    for entry in WHITELIST:
        target = root / entry
        if target.is_file():
            yield target, entry
            continue
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix in EXCLUDED_SUFFIXES:
                    continue
                yield path, path.relative_to(root).as_posix()


def build(version: str, out_dir: Path, *, require_version_match: bool = False) -> dict:
    version = normalize_version(version)
    if require_version_match:
        declared = declared_launcher_version()
        if declared != version:
            raise RuntimeError(
                f"release tag {version} does not match LAUNCHER_VERSION {declared}; "
                "update 统一启动器.py (and the about-page changelog) before releasing"
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    asset_name = f"launcher-{version}.zip"
    archive_path = out_dir / asset_name

    payload = list(iter_payload(PROJECT_ROOT))
    if not payload:
        raise RuntimeError("nothing to package")

    names = {arcname for _, arcname in payload}
    missing = [name for name in REQUIRED_FILES if name not in names]
    if missing:
        raise RuntimeError("payload is missing required file(s): " + ", ".join(missing))

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, arcname in payload:
            zf.write(path, arcname)

    digest = hashlib.sha256()
    with archive_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    manifest = {
        "version": version,
        "asset": asset_name,
        "size": archive_path.stat().st_size,
        "sha256": digest.hexdigest(),
        "files": len(payload),
        "replaceWhitelist": list(WHITELIST),
    }
    manifest_path = out_dir / "launcher-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"archive": str(archive_path), "manifest": str(manifest_path), **manifest}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release tag, e.g. v1.3.0")
    parser.add_argument("--out", default="dist", help="output directory")
    parser.add_argument(
        "--require-version-match",
        action="store_true",
        help="fail when --version differs from LAUNCHER_VERSION",
    )
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = build(
            args.version,
            Path(args.out),
            require_version_match=args.require_version_match,
        )
    except Exception as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    print(f"built {result['archive']}")
    print(f"  size   {result['size']} bytes")
    print(f"  sha256 {result['sha256']}")
    print(f"  files  {result['files']}")
    print(f"manifest {result['manifest']}")
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
