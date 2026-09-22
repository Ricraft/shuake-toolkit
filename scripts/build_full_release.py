# -*- coding: utf-8 -*-
"""Build the full runtime package used by first-run bootstrap.

The launcher self-update asset (``launcher-<version>.zip``) intentionally
contains only the launcher sources.  This builder produces the *full* package
for new users: launcher, scripts, local Autovisor sources, Yatori assets and
the first-run bootstrap entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

try:
    from .build_launcher_release import declared_launcher_version, normalize_version
except ImportError:  # pragma: no cover - direct script execution
    from build_launcher_release import declared_launcher_version, normalize_version

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ROOT_FILES = (
    "统一启动器.py",
    "requirements.txt",
    "requirements-optional.txt",
    "README.md",
    "bootstrap.py",
    "启动依赖.cmd",

)

ROOT_DIRS = (
    "src",
    "web",
    "scripts",
    "Autovisor",
    "Yatori",
)

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "node_modules",
    "runtime_deps",
    "logs",
    "log",
    "data",
    "output",
    "dist",
    "build",
    "tests",
    ".agents",
    ".agent-teams",
    ".codex",
    ".playwright-cli",
    ".github",
    ".update-staging",
    ".update-backup",
}

EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".exe", ".zip"}

EXCLUDED_RELATIVE_PATHS = {
    "Autovisor/configs.ini",
    "Autovisor/res/cookies.json",
    "Yatori/config.yaml",
}

REQUIRED_FILES = (
    "统一启动器.py",
    "requirements.txt",
    "bootstrap.py",
    "启动依赖.cmd",
    "src/web_action_service.py",
    "src/dependencies.py",
    "web/app.js",
    "web/现代启动器_UI_预览.html",
    "scripts/fetch_zhs_courses.py",
    "scripts/ai_connectivity_test.py",
    "Autovisor/Autovisor.py",
    "Autovisor/Autovisor_Multi.py",
    "Autovisor/Practice_Mode.py",
)


def iter_payload(root: Path):
    """Yield ``(path, arcname)`` for every file in the full package."""
    root = Path(root)
    for entry in ROOT_FILES:
        path = root / entry
        if path.is_file():
            yield path, entry

    for entry in ROOT_DIRS:
        target = root / entry
        if not target.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(target):
            current = Path(dirpath)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXCLUDED_DIR_NAMES
                and (current / name).relative_to(root).as_posix()
                not in EXCLUDED_RELATIVE_PATHS
            )
            for name in sorted(filenames):
                path = current / name
                relative = path.relative_to(root).as_posix()
                if path.suffix.lower() in EXCLUDED_SUFFIXES:
                    continue
                if relative in EXCLUDED_RELATIVE_PATHS:
                    continue
                if any(
                    relative == excluded
                    or relative.startswith(excluded.rstrip("/") + "/")
                    for excluded in EXCLUDED_RELATIVE_PATHS
                ):
                    continue
                yield path, relative


def build(
    version: str,
    out_dir: Path,
    *,
    root: Path | None = None,
    yatori_core: dict | None = None,
    require_version_match: bool = False,
) -> dict:
    root = Path(root or PROJECT_ROOT).resolve()
    version = normalize_version(version)
    if require_version_match:
        declared = declared_launcher_version()
        if declared != version:
            raise RuntimeError(
                f"release tag {version} does not match LAUNCHER_VERSION {declared}"
            )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    asset_name = f"shuake-toolkit-{version}.zip"
    archive_path = out_dir / asset_name

    payload = list(iter_payload(root))
    names = {arcname for _, arcname in payload}
    missing = [name for name in REQUIRED_FILES if name not in names]
    if missing:
        raise RuntimeError("full package is missing required file(s): " + ", ".join(missing))

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
        "fileList": sorted(names),
        "requiredFiles": list(REQUIRED_FILES),
    }
    if yatori_core:
        manifest["yatoriCore"] = {
            "url": str(yatori_core.get("url") or "").strip(),
            "sha256": str(yatori_core.get("sha256") or "").strip(),
            "size": yatori_core.get("size"),
            "version": str(yatori_core.get("version") or "").strip(),
        }

    manifest_path = out_dir / "full-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"archive": str(archive_path), "manifest": str(manifest_path), **manifest}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release tag, e.g. v1.5.0")
    parser.add_argument("--out", default="dist", help="output directory")
    parser.add_argument("--require-version-match", action="store_true")
    parser.add_argument("--yatori-core-url", default="")
    parser.add_argument("--yatori-core-sha256", default="")
    parser.add_argument("--yatori-core-size", type=int, default=None)
    parser.add_argument("--yatori-core-version", default="")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    yatori_core = None
    if args.yatori_core_url:
        yatori_core = {
            "url": args.yatori_core_url,
            "sha256": args.yatori_core_sha256,
            "size": args.yatori_core_size,
            "version": args.yatori_core_version,
        }

    try:
        result = build(
            args.version,
            Path(args.out),
            yatori_core=yatori_core,
            require_version_match=args.require_version_match,
        )
    except Exception as exc:
        print(f"full package build failed: {exc}", file=sys.stderr)
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

