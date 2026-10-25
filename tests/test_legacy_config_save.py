import inspect
import sys
import types
from dataclasses import asdict
from unittest.mock import patch

import config
import config_gui
import cvrp_api_server as api
from config_compat import VEHICLE_SCHEMA_DEFAULTS, ensure_vehicle_schema, reload_config_module


def legacy_source():
    vehicle_source = inspect.getsource(config.VehicleConfig)
    vehicle_source = "\n".join(line for line in vehicle_source.splitlines()
                               if line.strip().split(":", 1)[0] not in VEHICLE_SCHEMA_DEFAULTS)
    return ("from dataclasses import dataclass, field\nfrom typing import *\nfrom config import VehicleType\n"
            + vehicle_source + '\n\nclass SavedConfig:\n'
            '    def _create_default_vehicles(self) -> List[VehicleConfig]:\n'
            '        return [\n'
            '            VehicleConfig(\n'
            '                vehicle_type=VehicleType.INTERNAL_BUS,\n'
            '                capacity=321,\n'
            '                count=2,\n'
            '                service_time_minutes=13,\n'
            '            ),\n'
            '        ]\n')


def test_read_save_and_plain_reload_legacy_vehicle_config(tmp_path):
    path = tmp_path / "config.py"
    path.write_text(legacy_source(), encoding="utf-8")
    original = path.read_bytes()
    module = types.ModuleType("legacy_config_save_test")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    try:
        reload_config_module(module)
        assert path.read_bytes() == original
        vehicle = module.SavedConfig()._create_default_vehicles()[0]
        assert vehicle.service_time_mode == "fixed" and vehicle.service_time_minutes == 13
        gui = config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
        gui.cfg = config.MainConfig()
        gui.cfg.vehicles = [vehicle]
        values = {"vehicle.0." + key: value for key, value in asdict(vehicle).items()}
        values["vehicle.0.vehicle_type"] = "internal_bus"
        values["vehicle.0.service_time_mode"] = "formula"
        values["vehicle.0.service_seconds_per_unit"] = 7
        with patch.object(config_gui, "config", module), patch.object(config_gui, "_base_dir", str(tmp_path)):
            with patch.object(gui, "_depot_coords_from_choice", return_value=(42.7, 23.3)):
                assert gui._apply_to_config_file(values)
        # Plain execution, not the compatibility loader: catches missing schema
        # declarations after serialising new constructor keyword arguments.
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
        saved = module.SavedConfig()._create_default_vehicles()[0]
        assert saved.service_time_mode == "formula"
        assert saved.service_seconds_per_unit == 7
        assert saved.service_time_minutes == 13
        assert saved.capacity == 321 and saved.count == 2
        reload_config_module(module)
        assert module.SavedConfig()._create_default_vehicles()[0].service_time_mode == "formula"
    finally:
        sys.modules.pop(module.__name__, None)


def test_web_vehicle_save_adds_schema_and_keeps_existing_fields(tmp_path):
    path = tmp_path / "config.py"
    path.write_text(legacy_source(), encoding="utf-8")
    cfg = config.MainConfig()
    cfg.vehicles = [config.VehicleConfig(vehicle_type=config.VehicleType.INTERNAL_BUS,
                                       capacity=321, count=2, service_time_minutes=13,
                                       service_time_mode="formula", service_seconds_per_unit=7)]
    with patch.object(api.config_module, "__file__", str(path)):
        api._persist_web_gui_config(cfg, include_api=False, include_cvrp=False, include_vehicles=True)
    scope = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), scope)
    vehicle = scope["SavedConfig"]()._create_default_vehicles()[0]
    assert vehicle.service_time_mode == "formula" and vehicle.capacity == 321


def test_schema_upgrade_is_idempotent_and_preserves_explicit_defaults():
    source = legacy_source()
    upgraded = ensure_vehicle_schema(source)
    assert ensure_vehicle_schema(upgraded) == upgraded
    edited = upgraded.replace("service_time_mode: str = 'fixed'", "service_time_mode: str = 'formula'")
    assert ensure_vehicle_schema(edited) == edited
