import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from unittest.mock import patch

import config
import config_gui
import cvrp_api_server as api


class MultiTripConfigSurfaceTests(unittest.TestCase):
    def test_operational_config_has_valid_toggle_and_unique_vehicle_ids(self):
        cfg = config.get_config()
        self.assertIsInstance(cfg.cvrp.enable_multiple_trips, bool)
        ids = [vehicle.config_id for vehicle in cfg.vehicles]
        self.assertTrue(all(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(vehicle.reload_time_minutes >= 0 for vehicle in cfg.vehicles))

    def test_web_vehicle_round_trip_preserves_reload_fields(self):
        cfg = deepcopy(config.get_config())
        original = cfg.vehicles[0]
        original.reload_location = cfg.locations.vratza_depot_location
        original.reload_time_minutes = 37
        original.max_customers_per_day = 44

        payload = api._vehicle_to_web_dict(original)
        rebuilt = api._vehicle_from_payload(cfg, payload)

        self.assertEqual(rebuilt.config_id, original.config_id)
        self.assertEqual(rebuilt.reload_location, original.reload_location)
        self.assertEqual(rebuilt.reload_time_minutes, 37)
        self.assertEqual(rebuilt.max_customers_per_day, 44)

    def test_web_vehicle_start_is_a_named_depot_not_free_gps(self):
        cfg = deepcopy(config.get_config())
        cfg.locations.depot_locations = {
            **(cfg.locations.depot_locations or {}),
            "Тестово депо": (42.1234, 23.5678),
        }
        original = cfg.vehicles[0]
        original.start_location = (42.1234, 23.5678)

        payload = api._vehicle_to_web_dict(original, cfg)

        self.assertEqual("Тестово депо", payload["start_depot_name"])
        self.assertNotIn("start_location", payload)
        rebuilt = api._vehicle_from_payload(cfg, payload)
        self.assertEqual((42.1234, 23.5678), rebuilt.start_location)

    def test_config_id_patch_does_not_collapse_duplicate_vehicle_types(self):
        cfg = deepcopy(config.get_config())
        internal = [item for item in cfg.vehicles if item.vehicle_type == config.VehicleType.INTERNAL_BUS]
        self.assertGreaterEqual(len(internal), 1)
        if len(internal) == 1:
            duplicate = deepcopy(internal[0])
            duplicate.config_id = f"{internal[0].config_id}_test_duplicate"
            cfg.vehicles.append(duplicate)
            internal.append(duplicate)
        original_count = len(cfg.vehicles)

        api._patch_vehicles(cfg, [{"config_id": internal[0].config_id, "count": 9}])
        self.assertEqual(len(cfg.vehicles), original_count)
        matching = {item.config_id: item for item in cfg.vehicles}
        self.assertEqual(matching[internal[0].config_id].count, 9)
        self.assertEqual(matching[internal[1].config_id].count, internal[1].count)

        api._patch_vehicles(cfg, [{"vehicle_type": "internal_bus", "reload_time_minutes": 18}])
        self.assertEqual(len(cfg.vehicles), original_count)
        self.assertTrue(all(
            item.reload_time_minutes == 18
            for item in cfg.vehicles
            if item.vehicle_type == config.VehicleType.INTERNAL_BUS
        ))

    def test_web_general_save_does_not_persist_vehicle_section(self):
        cfg = deepcopy(config.get_config())
        fake_manager = types.SimpleNamespace(config=cfg)

        with (
            patch.object(api, "get_config", return_value=cfg),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py") as persist,
            patch.object(api.config_module, "config_manager", fake_manager),
        ):
            api._apply_web_gui_config_save({"cvrp": {"enable_multiple_trips": True}})

        self.assertTrue(fake_manager.config.cvrp.enable_multiple_trips)
        self.assertEqual(fake_manager.config.vehicles, cfg.vehicles)
        self.assertFalse(persist.call_args.kwargs["include_vehicles"])

    def test_web_global_save_persists_operational_sections_but_not_vehicles(self):
        cfg = deepcopy(config.get_config())
        original_vehicles = deepcopy(cfg.vehicles)
        cfg.api.web_gui_run_defaults_json = '{"output":{"enable_csv_output":true}}'
        fake_manager = types.SimpleNamespace(config=cfg)
        requested_solver = "or_tools" if cfg.cvrp.solver_type != "or_tools" else "pyvrp"
        requested_excel = not cfg.output.enable_excel_output

        with (
            patch.object(api, "get_config", return_value=cfg),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py") as persist,
            patch.object(api.config_module, "config_manager", fake_manager),
        ):
            result = api._apply_web_gui_config_save({
                "cvrp": {"solver_type": requested_solver},
                "output": {"enable_excel_output": requested_excel},
                "set_data": {"set_data_command": "WEB_GLOBAL_TEST"},
            })

        self.assertEqual("global_settings_excluding_vehicles", result["scope"])
        self.assertEqual(requested_solver, fake_manager.config.cvrp.solver_type)
        self.assertEqual(requested_excel, fake_manager.config.output.enable_excel_output)
        self.assertEqual("WEB_GLOBAL_TEST", fake_manager.config.set_data.set_data_command)
        self.assertEqual("", fake_manager.config.api.web_gui_run_defaults_json)
        self.assertEqual(original_vehicles, fake_manager.config.vehicles)
        self.assertEqual(
            {
                "include_api": True,
                "include_cvrp": True,
                "include_vehicles": False,
                "include_output": True,
                "include_set_data": True,
            },
            persist.call_args.kwargs,
        )

    def test_web_vehicle_save_persists_only_vehicle_section(self):
        cfg = deepcopy(config.get_config())
        original_solver = cfg.cvrp.solver_type
        vehicles = [api._vehicle_to_web_dict(vehicle, cfg) for vehicle in cfg.vehicles]
        vehicles[0]["reload_time_minutes"] = 25
        fake_manager = types.SimpleNamespace(config=cfg)

        with (
            patch.object(api, "get_config", return_value=cfg),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py") as persist,
            patch.object(api.config_module, "config_manager", fake_manager),
        ):
            result = api._apply_web_gui_vehicles_save({"vehicles": vehicles})

        self.assertEqual("vehicles_only", result["scope"])
        self.assertEqual(fake_manager.config.vehicles[0].reload_time_minutes, 25)
        self.assertEqual(fake_manager.config.cvrp.solver_type, original_solver)
        self.assertEqual(
            {
                "include_api": False,
                "include_cvrp": False,
                "include_vehicles": True,
            },
            persist.call_args.kwargs,
        )

        with self.assertRaises(ValueError):
            api._apply_web_gui_vehicles_save({
                "vehicles": vehicles,
                "cvrp": {"solver_type": "or_tools"},
            })

        invalid_gps_payload = deepcopy(vehicles)
        invalid_gps_payload[0].pop("start_depot_name", None)
        invalid_gps_payload[0]["start_location"] = "42.1, 23.2"
        with self.assertRaisesRegex(ValueError, "само чрез start_depot_name"):
            api._apply_web_gui_vehicles_save({"vehicles": invalid_gps_payload})

    def test_vehicle_file_persistence_leaves_api_and_cvrp_text_unchanged(self):
        cfg = deepcopy(config.get_config())
        cfg.vehicles[0].config_id = "isolated_vehicle_save"
        source = '''from dataclasses import dataclass\nfrom typing import List\n\n@dataclass\nclass APIConfig:\n    web_gui_title: str = "KEEP_API"\n\n@dataclass\nclass CVRPConfig:\n    solver_type: str = "KEEP_CVRP"\n\nclass ConfigManager:\n    def _create_default_vehicles(self) -> List[VehicleConfig]:\n        return [\n            VehicleConfig(\n                vehicle_type=VehicleType.INTERNAL_BUS,\n                capacity=1,\n                count=1,\n            ),\n        ]\n'''

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.py"
            config_path.write_text(source, encoding="utf-8")
            with (
                patch.object(api.config_module, "__file__", str(config_path)),
                patch.object(api, "_backup_config_py", return_value="backup.py"),
            ):
                api._persist_web_gui_config(
                    cfg,
                    include_api=False,
                    include_cvrp=False,
                    include_vehicles=True,
                )

            saved = config_path.read_text(encoding="utf-8")

        self.assertIn('web_gui_title: str = "KEEP_API"', saved)
        self.assertIn('solver_type: str = "KEEP_CVRP"', saved)
        self.assertIn('config_id="isolated_vehicle_save"', saved)

    def test_global_file_persistence_writes_operational_sections_without_vehicles(self):
        cfg = deepcopy(config.get_config())
        cfg.cvrp.solver_type = "or_tools"
        cfg.cvrp.parallel_first_solution_strategies = ["SAVINGS", "PATH_CHEAPEST_ARC"]
        cfg.cvrp.parallel_local_search_metaheuristics = ["TABU_SEARCH"]
        cfg.output.folium_tiles = "Test.GlobalTiles"
        cfg.set_data.set_data_command = "TEST_GLOBAL_CMD"
        source = Path(config.__file__).read_text(encoding="utf-8")

        vehicle_start = source.index("    def _create_default_vehicles")
        vehicle_end = source.find("\n    def ", vehicle_start + 10)
        original_vehicle_block = source[vehicle_start:vehicle_end if vehicle_end >= 0 else None]

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.py"
            config_path.write_text(source, encoding="utf-8")
            with (
                patch.object(api.config_module, "__file__", str(config_path)),
                patch.object(api, "_backup_config_py", return_value="backup.py"),
            ):
                api._persist_web_gui_config(
                    cfg,
                    include_api=False,
                    include_cvrp=True,
                    include_vehicles=False,
                    include_output=True,
                    include_set_data=True,
                )
                # A second save used to turn the inline list factory produced
                # by the first save into a quoted string and break Web load.
                api._persist_web_gui_config(
                    cfg,
                    include_api=False,
                    include_cvrp=True,
                    include_vehicles=False,
                    include_output=True,
                    include_set_data=True,
                )
            saved = config_path.read_text(encoding="utf-8")

        saved_vehicle_start = saved.index("    def _create_default_vehicles")
        saved_vehicle_end = saved.find("\n    def ", saved_vehicle_start + 10)
        saved_vehicle_block = saved[
            saved_vehicle_start:saved_vehicle_end if saved_vehicle_end >= 0 else None
        ]
        self.assertIn('solver_type: str = "or_tools"', saved)
        self.assertIn(
            "parallel_first_solution_strategies: List[str] = field(default_factory=lambda: ['SAVINGS', 'PATH_CHEAPEST_ARC'])",
            saved,
        )
        self.assertIn(
            "parallel_local_search_metaheuristics: List[str] = field(default_factory=lambda: ['TABU_SEARCH'])",
            saved,
        )
        self.assertIn('folium_tiles: str = "Test.GlobalTiles"', saved)
        self.assertIn('set_data_command: str = "TEST_GLOBAL_CMD"', saved)
        self.assertEqual(original_vehicle_block, saved_vehicle_block)

    def test_desktop_and_web_markup_expose_multi_trip_settings(self):
        cfg = config.get_config()
        gui = object.__new__(config_gui.ConfigGUI)
        literal = gui._vehicle_config_literal(cfg.vehicles[0])
        self.assertIn("config_id=", literal)
        self.assertIn("reload_location=", literal)
        self.assertIn("reload_time_minutes=", literal)
        self.assertIn("max_customers_per_day=", literal)

        page = api._web_gui_html(cfg.api, "127.0.0.1", cfg.api.api_port)
        self.assertIn('id="multiTripEnabled"', page)
        self.assertIn("Разреши повторни курсове", page)
        self.assertIn("Запази само бусовете глобално", page)
        self.assertIn('class="vehicle-card-header"', page)
        self.assertIn('class="vehicle-number"', page)
        self.assertIn("renumberVehicleCards", page)
        self.assertIn('apiFetch("/vehicles"', page)
        self.assertIn('data-field="start_depot_name"', page)
        self.assertIn("Начално депо", page)
        self.assertNotIn("Старт GPS", page)
        self.assertIn('data-field="reload_location"', page)
        self.assertIn('data-field="reload_time_minutes"', page)
        self.assertIn('id="outSaturdayBusPrefix"', page)
        self.assertIn('id="outSaturdayBusDigits"', page)
        self.assertIn("saturday_excel_bus_number_prefix", page)

        desktop_source = Path(config_gui.__file__).read_text(encoding="utf-8")
        self.assertIn('"output.saturday_excel_bus_number_prefix"', desktop_source)
        self.assertIn('"api.saturday_trigger_endpoint"', desktop_source)

    def test_web_settings_are_collapsed_panels_while_vehicles_stay_visible(self):
        cfg = config.get_config()
        page = api._web_gui_html(cfg.api, "127.0.0.1", cfg.api.api_port)

        self.assertIn('<section class="vehicles-section">', page)
        self.assertNotIn('<details class="vehicles-section"', page)
        self.assertEqual(3, page.count('class="settings-card settings-accordion'))
        for panel_name in ("solver", "output", "set-data"):
            self.assertIn(f'data-settings-panel="{panel_name}">', page)
            self.assertNotIn(f'data-settings-panel="{panel_name}" open', page)
        self.assertIn('content:"Разпъни"', page)
        self.assertIn('content:"Свий"', page)

    def test_desktop_incremental_vehicle_save_persists_daily_distance(self):
        gui = config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
        source = """\
        VehicleConfig(
            vehicle_type=VehicleType.INTERNAL_BUS,
            capacity=320,
            count=1,
            max_distance_km=100,
            max_time_hours=8,
            start_time_minutes=480,
        ),
        ]
"""
        values = {"vehicle.0.max_distance_km": "55"}

        updated = gui._replace_vehicle_field(
            source,
            "internal_bus",
            0,
            values,
            "vehicle.0",
        )

        self.assertIn("max_distance_km=55", updated)


if __name__ == "__main__":
    unittest.main()
