import ast
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from config import VehicleConfig, VehicleType, get_config
from input_handler import Customer
from service_time import (SERVICE_FIELDS, customer_service_seconds, formula_seconds,
                          service_settings, service_signature, validate_service_settings)


def vehicle(**kwargs):
    values = dict(vehicle_type=VehicleType.INTERNAL_BUS, capacity=100, count=1,
                  service_time_minutes=8, service_time_mode="formula",
                  service_base_seconds=30.0, service_seconds_per_unit=12.0,
                  service_quantity_units=1.0, service_max_seconds=600.0)
    values.update(kwargs)
    return VehicleConfig(**values)


def customer(volume=10, override=None):
    return Customer("one", "One", (42.01, 23.01), volume, "", service_time_minutes=override)


@pytest.mark.parametrize("quantity,expected", [(0, 30), (10, 150), (25.5, 336), (100, 600)])
def test_formula_base_fractional_quantity_and_cap(quantity, expected):
    assert customer_service_seconds(customer(quantity), vehicle()) == expected


def test_units_divisor_and_floor_not_round_or_ceil():
    v = vehicle(service_base_seconds=0, service_seconds_per_unit=30, service_quantity_units=100)
    assert formula_seconds(1022.9, v) == 306
    assert formula_seconds(1020, v) == 306
    assert formula_seconds(0.5, v) == 0
    v.service_max_seconds = 60.9
    assert formula_seconds(10000, v) == 60


@pytest.mark.parametrize("override,expected", [(0, 0), (1.5, 90), (25, 1500)])
def test_input_override_wins_even_above_cap(override, expected):
    assert customer_service_seconds(customer(100, override), vehicle()) == expected


@pytest.mark.parametrize("override", [None, "", "bad", float("nan"), float("inf"), -2])
def test_missing_or_invalid_override_uses_formula(override):
    assert customer_service_seconds(customer(10, override), vehicle()) == 150


def test_legacy_fixed_mode_unchanged():
    legacy = SimpleNamespace(service_time_minutes=7)
    assert customer_service_seconds(customer(200), legacy) == 420
    assert customer_service_seconds(customer(200, 0), legacy) == 0
    assert VehicleConfig(VehicleType.INTERNAL_BUS, 100, 1).service_time_mode == "fixed"


@pytest.mark.parametrize("key,value", [
    ("service_quantity_units", 0), ("service_quantity_units", -1),
    ("service_base_seconds", -0.1), ("service_max_seconds", float("nan")),
    ("service_seconds_per_unit", float("inf")), ("service_max_seconds", True),
    ("service_time_mode", "eval"),
])
def test_reject_invalid_settings(key, value):
    with pytest.raises(ValueError):
        validate_service_settings(vehicle(**{key: value}))


def test_profile_identity_includes_formula_parameters():
    assert service_signature(vehicle()) != service_signature(vehicle(service_seconds_per_unit=2))


def test_web_round_trip_and_config_literal():
    import cvrp_api_server as api
    cfg = deepcopy(get_config())
    original = vehicle(service_quantity_units=100, service_seconds_per_unit=30.5)
    payload = api._vehicle_to_web_dict(original)
    rebuilt = api._vehicle_from_payload(cfg, payload)
    api._validate_vehicle_configs([rebuilt])
    assert service_settings(rebuilt) == service_settings(original)
    source = api._vehicle_config_literal(rebuilt).strip().rstrip(",")
    parsed = eval(compile(ast.parse(source, mode="eval"), "vehicle", "eval"),
                  {"VehicleConfig": VehicleConfig, "VehicleType": VehicleType})
    assert service_settings(parsed) == service_settings(original)


def test_web_fractional_update_is_not_truncated_when_old_value_is_integer():
    import cvrp_api_server as api
    old = vehicle(service_seconds_per_unit=12)
    updated = api._vehicle_from_payload(get_config(), {"service_seconds_per_unit": 0.35}, old)
    assert updated.service_seconds_per_unit == 0.35


def test_desktop_controls_switch_and_live_preview():
    import tkinter as tk
    from tkinter import ttk
    from config_gui import ConfigGUI
    root = tk.Tk()
    root.withdraw()
    try:
        gui = ConfigGUI.__new__(ConfigGUI)
        gui.widgets, gui.controls, gui.control_default_states = {}, {}, {}
        panel = ttk.Frame(root)
        with patch.object(gui, "_bind_text_editing"), patch.object(gui, "_bind_combobox_scrolling"):
            gui._add_service_time_fields(panel, 0, "vehicle.0", vehicle(service_time_mode="fixed"))
        base = gui.controls["vehicle.0.service_base_seconds"]
        fixed = gui.controls["vehicle.0.service_time_minutes"]
        assert base.winfo_manager() == ""
        assert fixed.winfo_manager() == "grid"
        gui.widgets["vehicle.0.service_time_mode"].set("Формула")
        assert base.winfo_manager() == ""
        gui.controls["vehicle.0.formula_toggle"].invoke()
        assert base.winfo_manager() == "grid"
        assert fixed.winfo_manager() == ""
        gui.widgets["vehicle.0.service_quantity_units"].set("100")
        gui.widgets["vehicle.0.service_seconds_per_unit"].set("30")
        preview = base.master.grid_slaves(row=6)[0]
        assert "100 единици → 1 мин" in preview.cget("text")
        assert "За клиент: 30 сек + 30 сек за всеки 100 единици." in preview.cget("text")
        gui.widgets["vehicle.0.service_quantity_units"].set("0")
        assert "над 0" in preview.cget("text")
    finally:
        root.destroy()


def test_desktop_round_trip_and_legacy_config_update():
    from config_gui import ConfigGUI
    gui = ConfigGUI.__new__(ConfigGUI)
    gui.cfg = deepcopy(get_config())
    v = vehicle()
    values = {f"vehicle.0.{key}": value for key, value in asdict(v).items()}
    values["vehicle.0.vehicle_type"] = "internal_bus"
    values["vehicle.0.service_time_mode"] = "Формула"
    values["vehicle.0.service_seconds_per_unit"] = "0,35"
    with patch.object(gui, "_depot_coords_from_choice", return_value=(42, 23)):
        rebuilt = gui._vehicle_config_from_values(values, "vehicle.0")
        source = "vehicles = [\n" + gui._vehicle_config_literal(v) + "\n]"
        source = gui._replace_vehicle_field(source, "internal_bus", 0, values, "vehicle.0")
    assert rebuilt.service_seconds_per_unit == 0.35
    namespace = {"VehicleConfig": VehicleConfig, "VehicleType": VehicleType}
    exec(compile(source, "test_config", "exec"), namespace)
    assert service_settings(namespace["vehicles"][0]) == service_settings(rebuilt)


def test_grouped_order_volume_is_used_once():
    from test_input_handler_service_time import make_handler, make_record
    grouped = make_handler()._process_json_records([
        make_record("a", Volume=10), make_record("b", Volume=20),
    ])
    # The existing grouping stage supplies one visit with the summed quantity.
    assert len(grouped) == 1
    assert grouped[0].volume == 30
    assert customer_service_seconds(grouped[0], vehicle()) == 390


def test_pyvrp_edges_schedule_and_distinct_vehicle_formulas():
    from test_solver_time_consistency import make_solver, make_customer, make_vehicle
    c = make_customer(service_time_minutes=None)
    c.volume = 25.5
    v = make_vehicle()
    for key, value in service_settings(vehicle(service_seconds_per_unit=12.03)).items():
        setattr(v, key, value)
    solver = make_solver(customer=c, vehicle=v)
    expected = customer_service_seconds(c, v)
    model = solver._create_pyvrp_model()
    assert model.profiles[0].edges[-1].duration == 61 + expected
    entries, _, _ = solver._build_route_schedule_entries([c], (42, 23), v, (42, 23), start_time_seconds=0)
    assert entries[0]["service_time_minutes"] * 60 == expected
    second = deepcopy(v)
    second.config_id = "second"
    second.service_seconds_per_unit = 1
    solver.vehicle_configs.append(second)
    assert len(solver._create_pyvrp_model().profiles) == 2


def test_vroom_and_rust_use_customer_volume():
    from test_solver_time_consistency import make_solver, make_customer, make_vehicle
    from vroom_solver import VROOMSolver
    from vrp_rust_prototype import VRPRustPrototype
    c = make_customer(service_time_minutes=None)
    c.volume = 25.5
    v = make_vehicle()
    for key, value in service_settings(vehicle()).items():
        setattr(v, key, value)
    vroom = make_solver(VROOMSolver, customer=c, vehicle=v)
    payload = vroom.build_problem()
    assert payload["jobs"][0]["service_per_type"] == {"vroom_type_1": 336}
    rust = make_solver(VRPRustPrototype, customer=c, vehicle=v)
    matrix = rust._build_profile_matrix("formula", v, [(42, 23), c.coordinates])
    assert matrix["travelTimes"] == [0, 61, 397, 0]


def test_ortools_formula_is_used_for_feasibility_not_only_report():
    from test_ortools_multitrip import ORToolsMultiTripTests, make_config, make_vehicle
    fixture = ORToolsMultiTripTests()
    v = make_vehicle(service_time_minutes=120, max_time_hours=1,
                     service_time_mode="formula", service_base_seconds=0,
                     service_seconds_per_unit=12, service_quantity_units=1,
                     service_max_seconds=600)
    solver = fixture.make_solver(1, config=make_config(enable_multiple_trips=False), vehicle=v)
    result = solver.solve()
    assert result and result.is_feasible
    assert result.routes[0].schedule_entries[0]["service_time_minutes"] * 60 == 72


def test_output_fallback_uses_formula():
    from output_handler import _report_customer_service_minutes
    assert _report_customer_service_minutes(customer(25.5), 8, vehicle()) == 336 / 60


def test_tsp_formula_and_input_priority():
    from current_tsp import _prepare_customer_service_times, _tsp_customer_service_seconds
    c = customer(25.5)
    _prepare_customer_service_times([c], vehicle(), {}, 8)
    assert _tsp_customer_service_seconds(c, 8) == 336
    _prepare_customer_service_times([c], vehicle(), {"service_time_minutes": 2}, 2)
    assert _tsp_customer_service_seconds(c, 8) == 120
    c.service_time_minutes = 0
    _prepare_customer_service_times([c], vehicle(), {"service_time_minutes": 2}, 2)
    assert _tsp_customer_service_seconds(c, 8) == 0


def test_worker_formula_capability_prevents_silent_old_worker_result(tmp_path):
    import pyvrp_next_runtime as runtime
    from test_pyvrp_next_runtime import FAKE_WORKER, make_config
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    with pytest.raises(runtime.PyVRPNextRuntimeError, match="Формула"):
        runtime._solve_experimental(SimpleNamespace(vehicle_customers=[]), (42, 23),
                                    None, make_config(str(worker)), None, [SimpleNamespace(**service_settings(vehicle()))])


def test_warehouse_formula_does_not_use_fixed_time_to_cut_capacity():
    from warehouse_manager import WarehouseManager
    manager = WarehouseManager(main_config=get_config(), vehicle_configs=[vehicle()],
                               cvrp_config=SimpleNamespace(enable_multiple_trips=True))
    v = vehicle(service_time_minutes=120, service_base_seconds=0,
                service_seconds_per_unit=0, max_time_hours=1, reload_time_minutes=10)
    assert manager._vehicle_trip_upper_bound(v, 10, [customer(10)]) == 7
