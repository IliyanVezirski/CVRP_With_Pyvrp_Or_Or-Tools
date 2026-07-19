import types
import unittest
from copy import deepcopy
from dataclasses import asdict
from datetime import date
from unittest.mock import patch

import config
import config_gui
import cvrp_api_server as api
import main


def _solution_with_route():
    route = types.SimpleNamespace(total_volume=12.5)
    return types.SimpleNamespace(
        routes=[route],
        dropped_customers=[],
        total_distance_km=1.0,
        total_time_minutes=2.0,
        total_vehicles_used=1,
        fitness_score=3.0,
        is_feasible=True,
        total_served_volume=0.0,
    )


def _worker_args(solver_type):
    cvrp = config.CVRPConfig(
        solver_type=solver_type,
        enable_parallel_solving=False,
        pyvrp_display_progress=False,
    )
    locations = config.LocationConfig()
    return (object(), asdict(cvrp), asdict(locations), object(), [], 1)


class SolverDispatchTests(unittest.TestCase):
    def test_worker_rehydrates_dynamic_center_zones_before_sidecar_dispatch(self):
        solution = _solution_with_route()
        cvrp = config.CVRPConfig(
            solver_type="pyvrp_experimental",
            enable_parallel_solving=False,
            pyvrp_display_progress=False,
        )
        locations = config.LocationConfig(center_zones=[
            config.CenterZoneConfig(
                name="Видин",
                mode="circle",
                center_coords=(43.90630307, 22.6675415),
                radius_km=35.0,
                priority_vehicle_types=["special_bus"],
                restricted_vehicle_types=["center_bus"],
                discount_priority_vehicle=0.8,
            )
        ])
        args = (object(), asdict(cvrp), asdict(locations), object(), [], 1)

        with patch.object(main, "solve_cvrp_pyvrp_next", return_value=solution) as solve_next:
            main.solve_cvrp_worker(args)

        passed_locations = solve_next.call_args.kwargs["location_config"]
        self.assertIsInstance(
            passed_locations.center_zones[0],
            config.CenterZoneConfig,
        )
        multiplier, penalty = config.center_zone_cost_adjustment(
            (43.90630307, 22.6675415),
            config.VehicleType.SPECIAL_BUS,
            passed_locations,
        )
        self.assertAlmostEqual(0.8, multiplier)
        self.assertEqual(0, penalty)

    def test_experimental_backend_dispatches_to_sidecar_runtime(self):
        solution = _solution_with_route()
        solution.solver_version = "0.14.0a0"

        with patch.object(main, "solve_cvrp_pyvrp_next", return_value=solution) as solve_next:
            result = main.solve_cvrp_worker(_worker_args("pyvrp_experimental"))

        solve_next.assert_called_once()
        self.assertIs(result, solution)
        self.assertEqual(result.solver_requested, "pyvrp_experimental")
        self.assertEqual(result.solver_used, "pyvrp_experimental")
        self.assertEqual(result.solver_backend, "pyvrp_experimental")
        self.assertEqual(result.solver_version, "0.14.0a0")
        self.assertFalse(result.solver_fallback_used)

    def test_stable_backend_remains_separate(self):
        solution = _solution_with_route()
        with (
            patch.object(main, "solve_cvrp_pyvrp", return_value=solution) as solve_stable,
            patch.object(main, "solve_cvrp_pyvrp_next") as solve_next,
        ):
            result = main.solve_cvrp_worker(_worker_args("pyvrp"))

        solve_stable.assert_called_once()
        solve_next.assert_not_called()
        self.assertEqual(result.solver_requested, "pyvrp")
        self.assertEqual(result.solver_used, "pyvrp")

    def test_or_tools_backend_remains_separate(self):
        solution = _solution_with_route()
        fake_solver = types.SimpleNamespace(solve=lambda *_args, **_kwargs: solution)
        with (
            patch.object(main, "CVRPSolver", return_value=fake_solver) as solver_class,
            patch.object(main, "solve_cvrp_pyvrp_next") as solve_next,
        ):
            result = main.solve_cvrp_worker(_worker_args("or_tools"))

        solver_class.assert_called_once()
        solve_next.assert_not_called()
        self.assertEqual(result.solver_requested, "or_tools")
        self.assertEqual(result.solver_used, "or_tools")

    def test_vroom_backend_remains_separate(self):
        solution = _solution_with_route()
        solution.solver_version = "1.15.2"
        with (
            patch.object(main, "solve_cvrp_vroom", return_value=solution) as solve_vroom,
            patch.object(main, "solve_cvrp_pyvrp") as solve_stable,
            patch.object(main, "solve_cvrp_pyvrp_next") as solve_next,
            patch.object(main, "CVRPSolver") as ortools_solver,
        ):
            result = main.solve_cvrp_worker(_worker_args("vroom"))

        solve_vroom.assert_called_once()
        solve_stable.assert_not_called()
        solve_next.assert_not_called()
        ortools_solver.assert_not_called()
        self.assertEqual(result.solver_requested, "vroom")
        self.assertEqual(result.solver_used, "vroom")
        self.assertEqual(result.solver_backend, "vroom")
        self.assertEqual(result.solver_version, "1.15.2")

    def test_unknown_backend_is_rejected_instead_of_falling_through(self):
        with self.assertRaisesRegex(ValueError, "Неподдържан solver_type"):
            main.solve_cvrp_worker(_worker_args("pyvrp_typo"))

    def test_default_version_is_not_attached_to_a_different_fallback_backend(self):
        solution = _solution_with_route()
        solution.solver_used = "or_tools"
        main._set_solver_metadata(
            solution,
            requested="pyvrp",
            default_used="pyvrp",
            default_version="0.13.4",
        )
        self.assertEqual(solution.solver_version, "")
        self.assertTrue(solution.solver_fallback_used)


class SolverSettingsSurfaceTests(unittest.TestCase):
    def test_api_resolves_current_week_saturday_dynamic_date(self):
        self.assertEqual(
            "18/07/2026",
            api._resolve_dynamic_api_date("current_week_saturday", date(2026, 7, 13)),
        )
        self.assertEqual(
            "18/07/2026",
            api._resolve_dynamic_api_date("current_week_saturday", date(2026, 7, 18)),
        )
        self.assertEqual(
            "18/07/2026",
            api._resolve_dynamic_api_date("current_week_saturday", date(2026, 7, 19)),
        )
        self.assertEqual(
            "21/07/2026",
            api._resolve_dynamic_api_date("21/07/2026", date(2026, 7, 13)),
        )

    def test_api_run_applies_dynamic_date_only_to_request_config(self):
        global_date = config.get_config().input.json_override_date
        with patch.object(api, "_resolve_dynamic_api_date", return_value="18/07/2026"):
            override, applied, ignored = api._build_request_config_override({
                "settings": {
                    "input": {"json_override_date": "current_week_saturday"},
                }
            })

        self.assertEqual("18/07/2026", override.input.json_override_date)
        self.assertEqual("2026-07-18_събота", override.output._api_run_date_stamp)
        self.assertIn("input.json_override_date", applied)
        self.assertEqual([], ignored)
        self.assertEqual(global_date, config.get_config().input.json_override_date)

    def test_saturday_endpoint_builds_isolated_date_and_bus_numbering(self):
        base = config.get_config()
        original_date = base.input.json_override_date
        original_prefix = base.output.excel_bus_number_prefix
        original_digits = base.output.excel_bus_number_digits
        with patch.object(api, "_resolve_dynamic_api_date", return_value="18/07/2026"):
            override, applied, ignored = api._build_saturday_run_config_override({
                "settings": {
                    "output": {
                        "excel_bus_number_prefix": "must-not-win",
                    }
                }
            })

        self.assertEqual("18/07/2026", override.input.json_override_date)
        self.assertEqual(base.output.saturday_excel_bus_number_prefix, override.output.excel_bus_number_prefix)
        self.assertEqual(base.output.saturday_excel_bus_number_digits, override.output.excel_bus_number_digits)
        self.assertEqual("2026-07-18_събота", override.output._api_run_date_stamp)
        self.assertIn("input.json_override_date", applied)
        self.assertIn("output.excel_bus_number_prefix", applied)
        self.assertIn("output.excel_bus_number_digits", applied)
        self.assertEqual([], ignored)
        self.assertEqual(original_date, base.input.json_override_date)
        self.assertEqual(original_prefix, base.output.excel_bus_number_prefix)
        self.assertEqual(original_digits, base.output.excel_bus_number_digits)

    def test_health_exposes_saturday_endpoint_and_prefix(self):
        cfg = config.get_config()
        payload = api._api_health_payload(cfg.api, "127.0.0.1", cfg.api.api_port)
        self.assertTrue(payload["endpoints"]["run_saturday"].endswith("/run_saturday"))
        self.assertEqual(
            cfg.output.saturday_excel_bus_number_prefix,
            payload["capabilities"]["run_saturday"]["bus_number_prefix"],
        )

    def test_api_run_can_isolate_maps_and_excel_in_dynamic_date_folder(self):
        global_map = config.get_config().output.map_output_file
        global_routes_dir = config.get_config().output.routes_output_dir
        global_excel_dir = config.get_config().output.excel_output_dir
        with patch.object(api, "_resolve_dynamic_api_date", return_value="18/07/2026"):
            override, applied, ignored = api._build_request_config_override({
                "settings": {
                    "input": {"json_override_date": "current_week_saturday"},
                    "output": {
                        "map_output_file": r"G:\Hell\Bizant_2.0\special_runs\{run_date}\interactive_map.html",
                        "routes_output_dir": r"G:\Hell\Bizant_2.0\special_runs\{run_date}\routes",
                        "excel_output_dir": r"G:\Hell\Bizant_2.0\special_runs\{run_date}\excel",
                    },
                }
            })

        self.assertEqual(
            r"G:\Hell\Bizant_2.0\special_runs\2026-07-18\interactive_map.html",
            override.output.map_output_file,
        )
        self.assertEqual(
            r"G:\Hell\Bizant_2.0\special_runs\2026-07-18\routes",
            override.output.routes_output_dir,
        )
        self.assertEqual(
            r"G:\Hell\Bizant_2.0\special_runs\2026-07-18\excel",
            override.output.excel_output_dir,
        )
        self.assertIn("output.map_output_file", applied)
        self.assertIn("output.routes_output_dir", applied)
        self.assertIn("output.excel_output_dir", applied)
        self.assertEqual([], ignored)
        self.assertEqual(global_map, config.get_config().output.map_output_file)
        self.assertEqual(global_routes_dir, config.get_config().output.routes_output_dir)
        self.assertEqual(global_excel_dir, config.get_config().output.excel_output_dir)

    def test_api_accepts_experimental_and_rejects_unknown_solver(self):
        override, applied, ignored = api._build_request_config_override({
            "settings": {
                "solver_type": "pyvrp_experimental",
                "include_waiting_in_time_objective": False,
                "pyvrp_next_worker_timeout_seconds": 90,
                "pyvrp_next_fallback_to_stable": True,
                "cvrp": {"pyvrp_next_worker_path": "C:\\untrusted-worker.exe"},
            }
        })

        self.assertIsNotNone(override)
        self.assertEqual(override.cvrp.solver_type, "pyvrp_experimental")
        self.assertFalse(override.cvrp.time_objective_include_waiting)
        self.assertEqual(override.cvrp.pyvrp_next_worker_timeout_seconds, 90)
        self.assertTrue(override.cvrp.pyvrp_next_fallback_to_stable)
        self.assertEqual(
            override.cvrp.pyvrp_next_worker_path,
            config.get_config().cvrp.pyvrp_next_worker_path,
        )
        self.assertIn("cvrp.solver_type", applied)
        self.assertTrue(any("pyvrp_next_worker_path" in item for item in ignored))

        with self.assertRaisesRegex(ValueError, "Невалиден solver_type"):
            api._build_request_config_override({"settings": {"solver_type": "latest"}})

    def test_desktop_validation_knows_all_supported_solver_values(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.controls = {}
        gui._reset_validation_styles = lambda: None
        gui._mark_invalid_control = lambda _key: None
        common = {
            "locations.depot_location": "42.69, 23.23",
            "locations.center_location": "42.70, 23.30",
            "locations.vratza_depot_location": "43.21, 23.54",
            "locations.center_zone_mode": "circle",
            "cvrp.pyvrp_next_worker_timeout_seconds": "0",
        }

        for solver_type in config.CVRP_SOLVER_TYPES:
            errors, _warnings = gui._validate_values({**common, "cvrp.solver_type": solver_type})
            self.assertNotIn("cvrp.solver_type", {key for key, _message in errors})

        errors, _warnings = gui._validate_values({**common, "cvrp.solver_type": "latest"})
        self.assertIn("cvrp.solver_type", {key for key, _message in errors})

    def test_web_selector_is_permanent_and_run_settings_stay_isolated(self):
        cfg = deepcopy(config.get_config())
        page = api._web_gui_html(cfg.api, "127.0.0.1", cfg.api.api_port)
        self.assertIn('id="solverType"', page)
        self.assertIn('id="objectiveMetric"', page)
        self.assertIn('id="timeObjectiveIncludeWaiting"', page)
        self.assertIn('<option value="pyvrp_experimental">PyVRP 0.14</option>', page)
        self.assertIn('<option value="vroom">VROOM 1.15</option>', page)
        self.assertIn('id="vroomThreads"', page)
        self.assertIn('id="vroomExploration"', page)
        self.assertIn('solver_type:value("solverType")', page)
        self.assertIn('time_objective_include_waiting:bool("timeObjectiveIncludeWaiting")', page)
        self.assertIn("Запази глобално в config.py", page)
        self.assertIn("Запази само бусовете глобално", page)
        self.assertIn("Стартирай без запис", page)
        self.assertIn("body:JSON.stringify(collectConfigPayload())", page)

        fake_manager = types.SimpleNamespace(config=cfg)
        with (
            patch.object(api, "get_config", return_value=cfg),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py"),
            patch.object(api.config_module, "config_manager", fake_manager),
        ):
            api._apply_web_gui_config_save({
                "cvrp": {
                    "solver_type": "pyvrp_experimental",
                    "time_objective_include_waiting": False,
                },
            })

        self.assertEqual(fake_manager.config.cvrp.solver_type, "pyvrp_experimental")
        self.assertFalse(fake_manager.config.cvrp.time_objective_include_waiting)
        temporary, applied, ignored = api._build_web_run_config_override({
                "run_settings": {"cvrp": {"solver_type": "pyvrp_experimental"}},
            })
        self.assertEqual("pyvrp_experimental", temporary.cvrp.solver_type)
        self.assertIn("cvrp.solver_type", applied)
        self.assertEqual([], ignored)

        vroom_temporary, vroom_applied, _ = api._build_web_run_config_override({
            "run_settings": {
                "cvrp": {
                    "solver_type": "vroom",
                    "enable_multiple_trips": False,
                    "time_limit_seconds": 120,
                    "vroom_threads": 2,
                    "vroom_exploration_level": 5,
                }
            },
        })
        self.assertEqual("vroom", vroom_temporary.cvrp.solver_type)
        self.assertEqual(2, vroom_temporary.cvrp.vroom_threads)
        self.assertIn("cvrp.vroom_exploration_level", vroom_applied)

    def test_api_accepts_vroom_but_protects_worker_path(self):
        override, applied, ignored = api._build_request_config_override({
            "settings": {
                "solver_type": "vroom",
                "enable_multiple_trips": False,
                "vroom_threads": 3,
                "vroom_exploration_level": 5,
                "cvrp": {"vroom_worker_path": "C:\\untrusted-vroom.exe"},
            }
        })
        self.assertEqual("vroom", override.cvrp.solver_type)
        self.assertEqual(3, override.cvrp.vroom_threads)
        self.assertIn("cvrp.solver_type", applied)
        self.assertTrue(any("vroom_worker_path" in item for item in ignored))

    def test_api_response_exposes_backend_metadata(self):
        solution = types.SimpleNamespace(
            routes=[],
            dropped_customers=[],
            total_distance_km=0.0,
            total_time_minutes=0.0,
            total_vehicles_used=0,
            fitness_score=0.0,
            total_trips=0,
            second_trips_count=0,
            solver_requested="pyvrp_experimental",
            solver_used="pyvrp",
            solver_backend="pyvrp",
            solver_version="0.13.4",
            solver_fallback_used=True,
            solver_fallback_reason="worker missing",
        )
        response = main._solution_to_api_response(
            solution,
            types.SimpleNamespace(customers=[]),
            types.SimpleNamespace(vehicle_customers=[], warehouse_customers=[]),
            {},
            1.25,
        )

        self.assertEqual(response["solver_requested"], "pyvrp_experimental")
        self.assertEqual(response["solver_used"], "pyvrp")
        self.assertEqual(response["solver_backend"], "pyvrp")
        self.assertEqual(response["solver_version"], "0.13.4")
        self.assertTrue(response["solver_fallback_used"])
        self.assertEqual(response["solver_fallback_reason"], "worker missing")


if __name__ == "__main__":
    unittest.main()
