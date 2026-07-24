import unittest
from copy import deepcopy
from unittest.mock import patch

import cvrp_api_server as api
from config import get_config
from config_gui import ConfigGUI


class VRPWebAPITests(unittest.TestCase):
    def test_default_max_generations_fits_gui_and_api_validation(self):
        generations = get_config().cvrp.vrp_max_generations
        self.assertGreaterEqual(generations, 1)
        self.assertLessEqual(generations, 1_000_000_000_000)

    def test_desktop_gui_replaces_complete_underscored_integer_literal(self):
        editor = ConfigGUI.__new__(ConfigGUI)
        source = "    vrp_max_generations: int = 1_000_000_000_000\n"

        updated = editor._replace_field_value(
            source,
            "vrp_max_generations",
            "2000000",
            "int",
        )

        self.assertEqual("    vrp_max_generations: int = 2000000\n", updated)

    def test_web_page_exposes_vrp_selector_and_safe_controls(self):
        cfg = get_config()
        page = api._web_gui_html(cfg.api, "127.0.0.1", 8084)

        for fragment in (
            '<option value="vrp">VRP-Rust</option>',
            'id="vrpWorkerTimeout"',
            'id="vrpThreads"',
            'id="vrpMaxGenerations"',
            'id="vrpLogProgress"',
            'vrp_worker_timeout_seconds:requiredInt("vrpWorkerTimeout"',
            'vrp_threads:requiredInt("vrpThreads"',
            'vrp_max_generations:requiredInt("vrpMaxGenerations"',
            'vrp_log_progress:bool("vrpLogProgress")',
        ):
            self.assertIn(fragment, page)

    def test_vrp_worker_path_is_protected_everywhere(self):
        self.assertIn(("cvrp", "vrp_worker_path"), api._PROTECTED_REQUEST_SETTING_FIELDS)
        self.assertNotIn("vrp_worker_path", api._WEB_GUI_CVRP_FIELDS)
        self.assertNotIn("vrp_worker_path", api._WEB_RUN_CVRP_FIELDS)

        _override, _applied, ignored = api._build_request_config_override({
            "settings": {"cvrp": {"vrp_worker_path": r"C:\untrusted\worker.exe"}}
        })
        self.assertTrue(any("vrp_worker_path" in item and "protected" in item for item in ignored))

        with self.assertRaisesRegex(ValueError, "vrp_worker_path"):
            api._validate_web_run_settings_shape({
                "cvrp": {"vrp_worker_path": r"C:\untrusted\worker.exe"}
            })

    def test_vrp_safe_fields_work_for_temporary_and_global_settings(self):
        cfg = deepcopy(get_config())
        required_fields = {
            "vrp_worker_timeout_seconds",
            "vrp_threads",
            "vrp_max_generations",
            "vrp_log_progress",
        }
        if "vrp" not in api.CVRP_SOLVER_TYPES or not all(
            hasattr(cfg.cvrp, field_name) for field_name in required_fields
        ):
            self.skipTest("Core VRP config fields are not installed yet")

        run_payload = {
            "run_settings": {
                "cvrp": {
                    "solver_type": "vrp",
                    "vrp_worker_timeout_seconds": 900,
                    "vrp_threads": 8,
                    "vrp_max_generations": 2_000_000,
                    "vrp_log_progress": False,
                }
            }
        }
        with patch.object(api, "get_config", return_value=cfg):
            temporary, applied, ignored = api._build_web_run_config_override(run_payload)

        self.assertEqual("vrp", temporary.cvrp.solver_type)
        self.assertEqual(900, temporary.cvrp.vrp_worker_timeout_seconds)
        self.assertEqual(8, temporary.cvrp.vrp_threads)
        self.assertEqual(2_000_000, temporary.cvrp.vrp_max_generations)
        self.assertFalse(temporary.cvrp.vrp_log_progress)
        self.assertIn("cvrp.vrp_threads", applied)
        self.assertEqual([], ignored)

        with (
            patch.object(api, "get_config", return_value=cfg),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py"),
            patch.object(api.config_module.config_manager, "config", cfg),
        ):
            result = api._apply_web_gui_config_save(run_payload["run_settings"])

        self.assertEqual("global_settings_excluding_vehicles", result["scope"])
        self.assertIn("cvrp.vrp_max_generations", result["applied"])

    def test_vrp_numeric_limits_are_validated(self):
        cfg = deepcopy(get_config())
        if "vrp" not in api.CVRP_SOLVER_TYPES or not hasattr(cfg.cvrp, "vrp_threads"):
            self.skipTest("Core VRP config fields are not installed yet")

        cfg.cvrp.solver_type = "vrp"
        cfg.cvrp.vrp_threads = 257
        with self.assertRaisesRegex(ValueError, "vrp_threads"):
            api._validate_web_run_config(cfg)

        cfg.cvrp.vrp_threads = 0
        cfg.cvrp.vrp_worker_timeout_seconds = -1
        with self.assertRaisesRegex(ValueError, "vrp_worker_timeout_seconds"):
            api._validate_web_run_config(cfg)

        cfg.cvrp.vrp_worker_timeout_seconds = 0
        cfg.cvrp.vrp_max_generations = 0
        with self.assertRaisesRegex(ValueError, "vrp_max_generations"):
            api._validate_web_run_config(cfg)


if __name__ == "__main__":
    unittest.main()
