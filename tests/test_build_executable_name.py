import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_exe


class BuildExecutableNameTests(unittest.TestCase):
    def test_main_executable_is_named_bizant(self):
        self.assertEqual("Bizant", build_exe.MAIN_EXE_NAME)
        self.assertEqual("Bizant.exe", build_exe.MAIN_EXE_FILENAME)

    def test_generated_spec_and_version_metadata_use_bizant(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "generated.spec"
            version_path = Path(tmp) / "version.txt"
            with patch.object(build_exe, "SPEC_FILE", spec_path), patch.object(
                build_exe, "VERSION_FILE", version_path
            ):
                build_exe.create_spec_file()
                build_exe.create_version_info()

            self.assertIn('name="Bizant"', spec_path.read_text(encoding="utf-8"))
            metadata = version_path.read_text(encoding="utf-8")
            self.assertIn("OriginalFilename', u'Bizant.exe", metadata)
            self.assertIn("ProductName', u'Bizant", metadata)

    def test_build_removes_obsolete_main_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist_dir = root / "dist"
            build_dir = root / "build"
            dist_dir.mkdir()
            legacy_exe = dist_dir / "CVRP_Optimizer.exe"
            legacy_exe.write_bytes(b"legacy")

            def fake_run(_command):
                (dist_dir / "Bizant.exe").write_bytes(b"new")

            with patch.object(build_exe, "DIST_DIR", dist_dir), patch.object(
                build_exe, "BUILD_DIR", build_dir
            ), patch.object(build_exe, "_run", side_effect=fake_run):
                self.assertTrue(build_exe.build_exe())

            self.assertFalse(legacy_exe.exists())
            self.assertTrue((dist_dir / "Bizant.exe").is_file())

    def test_generated_launchers_start_bizant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "source"
            dist_dir = root / "dist"
            with patch.object(build_exe, "PROJECT_DIR", project_dir), patch.object(
                build_exe, "DIST_DIR", dist_dir
            ):
                build_exe.create_batch_files()

            for directory in (project_dir, dist_dir):
                for filename in (
                    "start_cvrp.bat",
                    "Settings.bat",
                    "start_api_server.bat",
                    "start_api_server_hidden.bat",
                ):
                    content = (directory / filename).read_text(encoding="utf-8-sig")
                    self.assertIn("Bizant.exe", content)
                    self.assertNotIn("CVRP_Optimizer.exe", content)


if __name__ == "__main__":
    unittest.main()
