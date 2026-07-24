import unittest
from unittest.mock import patch

import build_exe
import build_pyvrp_next_worker
import build_vroom_worker
import build_vrp_rust_worker


class WorkerBuildPackagingTests(unittest.TestCase):
    def test_all_companion_workers_use_onefile_packaging(self):
        for module in (
            build_pyvrp_next_worker,
            build_vroom_worker,
            build_vrp_rust_worker,
        ):
            with self.subTest(module=module.__name__):
                command = module._build_command()
                self.assertIn("--onefile", command)
                self.assertNotIn("--onedir", command)
                self.assertNotIn("--contents-directory", command)

    def test_release_worker_paths_are_flat_executables(self):
        workers = (
            build_exe.PYVRP_NEXT_WORKER,
            build_exe.VROOM_WORKER,
            build_exe.VRP_RUST_WORKER,
        )
        for worker in workers:
            with self.subTest(worker=worker):
                self.assertEqual(".exe", worker.suffix.lower())
                self.assertNotEqual(worker.stem, worker.parent.name)

    def test_main_reports_failure_when_required_worker_build_fails(self):
        with patch.object(build_exe, "check_dependencies", return_value=True), patch.object(
            build_exe, "create_version_info"
        ), patch.object(build_exe, "create_spec_file"), patch.object(
            build_exe, "build_exe", return_value=True
        ), patch.object(
            build_exe, "build_pyvrp_next_worker", return_value=False
        ), patch.object(
            build_exe, "build_vroom_worker"
        ) as vroom_build, patch.object(
            build_exe, "build_vrp_rust_worker"
        ) as vrp_rust_build, patch.object(
            build_exe, "create_batch_files"
        ) as create_batch_files, patch.object(
            build_exe, "copy_runtime_files"
        ) as copy_runtime_files:
            self.assertEqual(1, build_exe.main())

        vroom_build.assert_not_called()
        vrp_rust_build.assert_not_called()
        create_batch_files.assert_not_called()
        copy_runtime_files.assert_not_called()


if __name__ == "__main__":
    unittest.main()
