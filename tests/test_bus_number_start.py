from types import SimpleNamespace
from unittest.mock import patch

import pytest
import cvrp_api_server as api
import config_gui
from config import MainConfig, VehicleType
from cvrp_solver import Route, CVRPSolution
from setdata_client import build_set_data_rows
from vehicle_numbering import format_route_bus_number, validate_bus_number_start
from tests.test_missing_gps_unserved import record
from input_handler import InputHandler


def test_start_five_and_legacy_default():
    output = SimpleNamespace(excel_bus_number_prefix="10045010", excel_bus_number_digits=2)
    assert format_route_bus_number(output, 0) == "1004501001"
    output.excel_bus_number_start = 5
    assert [format_route_bus_number(output, i) for i in range(3)] == ["1004501005", "1004501006", "1004501007"]


def test_center_range_stays_separate_and_collisions_are_skipped():
    output = SimpleNamespace(excel_bus_number_prefix="10045010", excel_bus_number_digits=2,
                             excel_bus_number_start=5, center_bus_numbering_enabled=True,
                             center_bus_numbering_start_id=1004501006)
    routes = [Route(VehicleType.INTERNAL_BUS, 0, [], (42, 23)),
              Route(VehicleType.INTERNAL_BUS, 0, [], (42, 23), trip_number=2),
              Route(VehicleType.CENTER_BUS, 0, [], (42, 23))]
    assert [format_route_bus_number(output, i, route, routes) for i, route in enumerate(routes)] == ["1004501005", "1004501007", "1004501006"]


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "5.0", "bad", 1000000000])
def test_invalid_start_is_rejected(value):
    with pytest.raises(ValueError):
        validate_bus_number_start(value)


def test_setdata_uses_same_start_as_reports_and_maps():
    cfg = MainConfig()
    cfg.output.excel_bus_number_start = 5
    cfg.output.excel_bus_number_prefix = "10045010"
    cfg.output.center_bus_numbering_enabled = False
    cfg.set_data.set_data_id_grafik_template = "{bus_number}"
    clients = InputHandler(cfg).load_data_from_json_records([record(GPS="42.7,23.3")]).customers
    route = Route(VehicleType.INTERNAL_BUS, 0, clients, (42, 23))
    rows = build_set_data_rows(CVRPSolution([route], [], 0, 0, 1, 0, True), cfg)
    assert rows[0]["IdGrafik"] == format_route_bus_number(cfg.output, 0, route, [route]) == "1004501005"


def test_web_legacy_profile_and_desktop_save(tmp_path):
    cfg = MainConfig()
    cfg.output = SimpleNamespace(**{k: v for k, v in vars(cfg.output).items() if k != "excel_bus_number_start"})
    assert api._web_run_settings_from_config(cfg)["output"]["excel_bus_number_start"] == 1
    with patch.object(api, "get_config", return_value=cfg):
        updated, _, ignored = api._build_web_run_config_override({"run_settings": {"output": {"excel_bus_number_start": 5}}})
    assert not ignored
    assert updated.output.excel_bus_number_start == 5
    assert not hasattr(cfg.output, "excel_bus_number_start")
    path = tmp_path / "config.py"
    path.write_text('from dataclasses import dataclass\n@dataclass\nclass OutputConfig:\n    excel_bus_number_digits: int = 2\n', encoding="utf-8")
    gui = config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
    gui.cfg = cfg
    gui.cfg.vehicles = []
    with patch.object(config_gui, "_base_dir", str(tmp_path)):
        gui._apply_to_config_file({"output.excel_bus_number_start": "5"})
    scope = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), scope)
    assert scope["OutputConfig"]().excel_bus_number_start == 5


def test_frozen_loader_accepts_legacy_vehicle_schema_without_rewriting_settings():
    from main_exe import _compile_external_config
    source = 'from dataclasses import dataclass\n@dataclass\nclass VehicleConfig:\n    service_time_minutes: int = 17\n    max_time_hours: float = 7.5\n'
    namespace = {}
    exec(_compile_external_config(source, "legacy_config.py"), namespace)
    vehicle = namespace["VehicleConfig"]()
    assert vehicle.service_time_minutes == 17 and vehicle.max_time_hours == 7.5
    assert vehicle.service_time_mode == "fixed"
    assert namespace["VehicleConfig"](service_time_mode="formula").service_time_mode == "formula"
