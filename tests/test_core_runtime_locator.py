import os
import tempfile
import unittest
from pathlib import Path

from src.core_runtime_locator import CoreRuntimeLocator


class CoreRuntimeLocatorTests(unittest.TestCase):
    def _locator(self, base_dir):
        return CoreRuntimeLocator(
            base_dir,
            yatori_entry_files=("yatori-go-console.exe", "start.bat"),
            autovisor_executable_entry_files=("Autovisor.exe", "AUto.exe"),
            autovisor_script_entry_files=(
                "Autovisor_Multi.py",
                "Autovisor.py",
                "main.py",
            ),
        )

    def test_standard_directories_take_priority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            standard_yatori = root / "Yatori"
            nested_yatori = (
                root
                / "yatori-go-console.v9"
                / "yatori-go-console.v9"
                / "command"
            )
            standard_autovisor = root / "Autovisor"
            alternate_autovisor = root / "AUto"
            for directory in (
                standard_yatori,
                nested_yatori,
                standard_autovisor,
                alternate_autovisor,
            ):
                directory.mkdir(parents=True)
            (standard_yatori / "start.bat").touch()
            (nested_yatori / "yatori-go-console.exe").touch()
            (standard_autovisor / "Autovisor.py").touch()
            (alternate_autovisor / "Autovisor.exe").touch()

            locator = self._locator(root)

            self.assertEqual(
                locator.find_yatori_path(),
                os.path.normpath(standard_yatori),
            )
            self.assertEqual(
                locator.find_autovisor_path(),
                os.path.normpath(standard_autovisor),
            )

    def test_nested_and_alternate_installations_are_discovered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested_yatori = (
                root
                / "yatori-go-console.v9"
                / "yatori-go-console.v9"
                / "command"
            )
            alternate_autovisor = root / "AUto-release" / "AUto-current"
            nested_yatori.mkdir(parents=True)
            alternate_autovisor.mkdir(parents=True)
            (nested_yatori / "yatori-go-console.exe").touch()
            (alternate_autovisor / "main.py").touch()

            locator = self._locator(root)

            self.assertEqual(
                locator.find_yatori_path(),
                os.path.normpath(nested_yatori),
            )
            self.assertEqual(
                locator.find_autovisor_path(),
                os.path.normpath(alternate_autovisor),
            )

    def test_missing_installations_return_stable_default_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            locator = self._locator(temp_dir)

            self.assertEqual(
                locator.find_yatori_path(),
                os.path.join(os.path.abspath(temp_dir), "Yatori"),
            )
            self.assertEqual(
                locator.find_autovisor_path(),
                os.path.join(os.path.abspath(temp_dir), "Autovisor"),
            )

    def test_yatori_command_prefers_executable_then_batch_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch_file = root / "start.bat"
            executable = root / "yatori-go-console.exe"
            batch_file.touch()
            locator = self._locator(root)

            self.assertEqual(
                locator.get_yatori_command(root),
                (["cmd", "/c", str(batch_file)], str(batch_file)),
            )

            executable.touch()
            self.assertEqual(
                locator.get_yatori_command(root),
                ([str(executable)], str(executable)),
            )

    def test_autovisor_executable_has_priority_over_scripts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Autovisor.py").touch()
            executable = root / "AUto.exe"
            executable.touch()

            result = self._locator(root).get_autovisor_entry_path(root, False)

            self.assertEqual(
                result,
                (str(executable), executable.name, True, True),
            )

    def test_autovisor_script_selection_respects_multi_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            single = root / "Autovisor.py"
            multiple = root / "Autovisor_Multi.py"
            single.touch()
            multiple.touch()
            locator = self._locator(root)

            self.assertEqual(
                locator.get_autovisor_entry_path(root, False),
                (str(single), single.name, True, False),
            )
            self.assertEqual(
                locator.get_autovisor_entry_path(root, True),
                (str(multiple), multiple.name, True, False),
            )

    def test_missing_autovisor_entry_reports_expected_preference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            locator = self._locator(temp_dir)

            self.assertEqual(
                locator.get_autovisor_entry_path(temp_dir, False),
                (None, "Autovisor.py", False, False),
            )
            self.assertEqual(
                locator.get_autovisor_entry_path(temp_dir, True),
                (None, "Autovisor_Multi.py", False, False),
            )


if __name__ == "__main__":
    unittest.main()
