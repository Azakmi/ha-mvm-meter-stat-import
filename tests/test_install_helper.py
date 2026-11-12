"""Tests for the Home Assistant integration install/update helper."""

from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY_ROOT / "scripts" / "ha-integration"
SOURCE = REPOSITORY_ROOT / "custom_components" / "mvm_meter_stat_import"


class InstallHelperTests(unittest.TestCase):
    """Verify installation is explicit and updates stay within the integration."""

    def run_helper(self, action: str, config: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(HELPER), action, str(config)],
            capture_output=True,
            check=False,
            text=True,
        )

    def test_install_copies_integration_to_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir)

            result = self.run_helper("install", config)

            self.assertEqual(result.returncode, 0, result.stderr)
            installed = config / "custom_components" / "mvm_meter_stat_import"
            self.assertEqual(
                {path.name for path in installed.iterdir()},
                {path.name for path in SOURCE.iterdir()},
            )
            self.assertTrue((installed / "manifest.json").is_file())

    def test_install_refuses_existing_integration_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir)
            target = config / "custom_components" / "mvm_meter_stat_import"
            target.mkdir(parents=True)
            sentinel = target / "keep.txt"
            sentinel.write_text("unchanged", encoding="utf-8")

            result = self.run_helper("install", config)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse((target / "manifest.json").exists())

    def test_update_replaces_integration_files_but_preserves_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir)
            target = config / "custom_components" / "mvm_meter_stat_import"
            target.mkdir(parents=True)
            (target / "manifest.json").write_text("old copy", encoding="utf-8")
            extra = target / "local-note.txt"
            extra.write_text("keep", encoding="utf-8")
            other_integration = config / "custom_components" / "other_integration"
            other_integration.mkdir()
            other_file = other_integration / "sentinel.txt"
            other_file.write_text("untouched", encoding="utf-8")

            result = self.run_helper("update", config)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (target / "manifest.json").read_text(encoding="utf-8"),
                (SOURCE / "manifest.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(extra.read_text(encoding="utf-8"), "keep")
            self.assertEqual(other_file.read_text(encoding="utf-8"), "untouched")

    def test_update_requires_existing_integration_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_helper("update", Path(temp_dir))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("use 'install' first", result.stderr)


if __name__ == "__main__":
    unittest.main()
