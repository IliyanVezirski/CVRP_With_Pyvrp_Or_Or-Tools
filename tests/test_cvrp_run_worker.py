import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import cvrp_run_worker
from config import get_config


class CVRPRunWorkerTests(unittest.TestCase):
    def _paths(self, temp_dir: str) -> tuple[Path, Path]:
        root = Path(temp_dir)
        return root / "request.json", root / "result.json"

    def test_worker_applies_full_request_and_writes_result_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path, output_path = self._paths(temp_dir)
            request = {
                "run_settings": {
                    "output": {"enable_excel_output": False},
                    "set_data": {"enable_set_data_upload": True},
                }
            }
            input_path.write_text(json.dumps(request), encoding="utf-8")

            config_override = object()
            received = {}

            def build_override(worker_request):
                received["request"] = worker_request
                return (
                    config_override,
                    ["output.enable_excel_output", "set_data.enable_set_data_upload"],
                    [],
                )

            fake_server_module = types.ModuleType("cvrp_api_server")
            fake_server_module._build_web_run_config_override = build_override

            with (
                patch.dict(sys.modules, {"cvrp_api_server": fake_server_module}),
                patch.object(
                    cvrp_run_worker,
                    "run_optimization",
                    return_value={"status": "ok", "routes_count": 2},
                ) as run_optimization,
            ):
                exit_code = cvrp_run_worker.run_worker_from_files(str(input_path), str(output_path))

            self.assertEqual(exit_code, 0)
            self.assertEqual(received["request"], request)
            run_optimization.assert_called_once_with(config_override=config_override)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                result["settings_overrides"],
                ["output.enable_excel_output", "set_data.enable_set_data_upload"],
            )
            self.assertEqual(result["ignored_settings"], [])

    def test_worker_uses_real_builder_without_mutating_global_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path, output_path = self._paths(temp_dir)
            global_config = get_config()
            original_excel_setting = global_config.output.enable_excel_output
            requested_excel_setting = not original_excel_setting
            request = {
                "run_settings": {
                    "output": {"enable_excel_output": requested_excel_setting},
                    "set_data": {"enable_set_data_upload": False},
                }
            }
            input_path.write_text(json.dumps(request), encoding="utf-8")

            with (
                patch.object(global_config.api, "web_gui_run_defaults_json", ""),
                patch.object(
                    cvrp_run_worker,
                    "run_optimization",
                    return_value={"status": "ok"},
                ) as run_optimization,
            ):
                exit_code = cvrp_run_worker.run_worker_from_files(str(input_path), str(output_path))

            self.assertEqual(exit_code, 0)
            config_override = run_optimization.call_args.kwargs["config_override"]
            self.assertIsNot(config_override, global_config)
            self.assertEqual(config_override.output.enable_excel_output, requested_excel_setting)
            self.assertEqual(global_config.output.enable_excel_output, original_excel_setting)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("output.enable_excel_output", result["settings_overrides"])
            self.assertIn("set_data.enable_set_data_upload", result["settings_overrides"])

    def test_worker_rejects_missing_run_settings_and_writes_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path, output_path = self._paths(temp_dir)
            input_path.write_text(
                json.dumps({"output": {"enable_excel_output": False}}),
                encoding="utf-8",
            )

            with patch.object(cvrp_run_worker, "run_optimization") as run_optimization:
                with redirect_stderr(io.StringIO()):
                    exit_code = cvrp_run_worker.run_worker_from_files(str(input_path), str(output_path))

            self.assertEqual(exit_code, 1)
            run_optimization.assert_not_called()
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["type"], "web_cvrp_run")
            self.assertIn("run_settings", result["error"])

    def test_worker_serialises_builder_validation_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path, output_path = self._paths(temp_dir)
            input_path.write_text(
                json.dumps({"run_settings": {"output": {"unknown": True}}}),
                encoding="utf-8",
            )

            fake_server_module = types.ModuleType("cvrp_api_server")

            def reject(_worker_request):
                raise ValueError("Unsupported web run setting: output.unknown")

            fake_server_module._build_web_run_config_override = reject

            with (
                patch.dict(sys.modules, {"cvrp_api_server": fake_server_module}),
                patch.object(cvrp_run_worker, "run_optimization") as run_optimization,
            ):
                with redirect_stderr(io.StringIO()):
                    exit_code = cvrp_run_worker.run_worker_from_files(str(input_path), str(output_path))

            self.assertEqual(exit_code, 1)
            run_optimization.assert_not_called()
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "error")
            self.assertIn("output.unknown", result["error"])


if __name__ == "__main__":
    unittest.main()
