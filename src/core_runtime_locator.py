"""Locate installed core directories and select their runnable entry files."""

from __future__ import annotations

import glob
import os
from collections.abc import Iterable


class CoreRuntimeLocator:
    """Resolve Yatori and Autovisor installations without launcher state."""

    def __init__(
        self,
        base_dir,
        *,
        yatori_entry_files,
        autovisor_executable_entry_files,
        autovisor_script_entry_files,
    ):
        self.base_dir = os.path.abspath(os.fspath(base_dir))
        self.yatori_entry_files = tuple(yatori_entry_files)
        self.autovisor_executable_entry_files = tuple(
            autovisor_executable_entry_files
        )
        self.autovisor_script_entry_files = tuple(autovisor_script_entry_files)
        self.autovisor_entry_files = (
            self.autovisor_executable_entry_files
            + self.autovisor_script_entry_files
        )

    @staticmethod
    def directory_has_any_file(directory, file_names: Iterable[str]) -> bool:
        return os.path.isdir(directory) and any(
            os.path.exists(os.path.join(directory, name)) for name in file_names
        )

    @staticmethod
    def _expanded_candidates(candidates):
        for candidate in candidates:
            if "*" in candidate:
                yield from sorted(glob.glob(candidate))
            else:
                yield candidate

    def find_existing_dir(self, candidates, required_files):
        seen = set()
        for candidate in self._expanded_candidates(candidates):
            normalized = os.path.normpath(candidate)
            identity = os.path.normcase(normalized)
            if identity in seen:
                continue
            seen.add(identity)
            if self.directory_has_any_file(normalized, required_files):
                return normalized
        return None

    def find_runtime_path(self, standard_dir, patterns, required_files):
        default_path = os.path.join(self.base_dir, standard_dir)
        return (
            self.find_existing_dir(
                [default_path, *patterns],
                required_files,
            )
            or default_path
        )

    def find_yatori_path(self):
        return self.find_runtime_path(
            "Yatori",
            [
                os.path.join(
                    self.base_dir,
                    "yatori-go-console*",
                    "yatori-go-console*",
                    "command",
                ),
                os.path.join(
                    self.base_dir,
                    "yatori-go-console*",
                    "command",
                ),
                os.path.join(self.base_dir, "yatori-go-console*"),
            ],
            self.yatori_entry_files,
        )

    def find_autovisor_path(self):
        candidates = [
            os.path.join(self.base_dir, "Autovisor"),
            os.path.join(self.base_dir, "AUto"),
            os.path.join(self.base_dir, "Auto"),
        ]
        for prefix in ("Autovisor", "AUto", "Auto"):
            candidates.extend(
                [
                    os.path.join(self.base_dir, f"{prefix}*", f"{prefix}*"),
                    os.path.join(self.base_dir, f"{prefix}*"),
                ]
            )
        return (
            self.find_existing_dir(candidates, self.autovisor_entry_files)
            or os.path.join(self.base_dir, "Autovisor")
        )

    def get_yatori_command(self, yatori_path):
        executable = os.path.join(yatori_path, "yatori-go-console.exe")
        if os.path.exists(executable):
            return [executable], executable

        batch_file = os.path.join(yatori_path, "start.bat")
        if os.path.exists(batch_file):
            return ["cmd", "/c", batch_file], batch_file

        return None, None

    def get_autovisor_entry_path(self, autovisor_path, multi_mode):
        for entry_name in self.autovisor_executable_entry_files:
            entry_path = os.path.join(autovisor_path, entry_name)
            if os.path.exists(entry_path):
                return (
                    entry_path,
                    entry_name,
                    entry_name.lower().endswith(".exe"),
                    True,
                )

        ordered_entries = list(self.autovisor_script_entry_files)
        preferred = "Autovisor_Multi.py" if multi_mode else "Autovisor.py"
        if preferred in ordered_entries:
            ordered_entries.remove(preferred)
        ordered_entries.insert(0, preferred)

        for entry_name in ordered_entries:
            entry_path = os.path.join(autovisor_path, entry_name)
            if os.path.exists(entry_path):
                return (
                    entry_path,
                    entry_name,
                    preferred == entry_name,
                    entry_path.lower().endswith(".exe"),
                )

        return None, preferred, False, False
