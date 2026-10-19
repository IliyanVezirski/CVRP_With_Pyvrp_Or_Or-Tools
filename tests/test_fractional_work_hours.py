from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch

import pytest
import cvrp_api_server as api
from config import get_config, VehicleConfig, VehicleType
from config_gui import ConfigGUI
from service_time import parse_work_hours


@pytest.mark.parametrize('value', [7.5, '7.5', '7,5', ' 7,50 '])
def test_parse_decimal_hours(value):
    assert parse_work_hours(value) * 3600 == 27000


@pytest.mark.parametrize('value', [None, '', True, 0, -1, 'nan', 'inf', '7:30', 'bad'])
def test_reject_invalid_hours(value):
    with pytest.raises(ValueError):
        parse_work_hours(value)


def test_web_legacy_integer_template_accepts_fractional_hours_and_round_trips():
    cfg = deepcopy(get_config())
    old = cfg.vehicles[0]
    old.max_time_hours = 8
    updated = api._vehicle_from_payload(cfg, {'max_time_hours': '7,5'}, old)
    api._validate_vehicle_configs([updated])
    assert updated.max_time_hours == 7.5
    assert api._vehicle_to_web_dict(updated, cfg)['max_time_hours'] == 7.5
    assert 'max_time_hours=7.5,' in api._vehicle_config_literal(updated)
    assert old.max_time_hours == 8


def test_desktop_full_and_incremental_save_preserve_decimal_hours():
    gui = ConfigGUI.__new__(ConfigGUI)
    gui.cfg = deepcopy(get_config())
    original = gui.cfg.vehicles[0]
    original.max_time_hours = 8.25
    values = {f'vehicle.0.{k}': v for k,v in asdict(original).items()}
    values['vehicle.0.max_time_hours'] = '7,5'
    values['vehicle.0.vehicle_type'] = original.vehicle_type.value
    with patch.object(gui, '_depot_coords_from_choice', return_value=(42,23)):
        updated = gui._vehicle_config_from_values(values, 'vehicle.0')
    assert updated.max_time_hours == 7.5
    literal = gui._vehicle_config_literal(updated)
    assert 'max_time_hours=7.5,' in literal
    source = 'vehicles = [\n' + gui._vehicle_config_literal(original) + '\n]'
    source = gui._replace_vehicle_field(source, original.vehicle_type.value, 0,
        {'vehicle.0.max_time_hours':'7,5'}, 'vehicle.0')
    namespace = {'VehicleConfig':VehicleConfig,'VehicleType':VehicleType}
    exec(compile(source, 'test', 'exec'), namespace)
    assert namespace['vehicles'][0].max_time_hours == 7.5


def test_pyvrp_and_vroom_models_use_27000_seconds():
    from test_solver_time_consistency import make_vehicle, make_solver
    from vroom_solver import VROOMSolver
    vehicle = make_vehicle()
    vehicle.max_time_hours = 7.5
    data = make_solver(vehicle=vehicle)._create_pyvrp_model().data()
    assert data.vehicle_type(0).max_duration == 27000
    payload = make_solver(VROOMSolver, vehicle=vehicle).build_problem()
    start, end = payload['vehicles'][0]['time_window']
    assert end - start == 27000


def test_rust_shift_uses_27000_seconds():
    from test_solver_time_consistency import make_vehicle, make_solver
    from vrp_rust_prototype import VRPRustPrototype
    vehicle = make_vehicle()
    vehicle.max_time_hours = 7.5
    solver = make_solver(VRPRustPrototype, vehicle=vehicle)
    request = solver.build_request()
    shift = request['problem']['fleet']['vehicles'][0]['shifts'][0]
    assert solver._seconds_from_iso(shift['end']['latest']) - solver._seconds_from_iso(shift['start']['earliest']) == 27000


@pytest.mark.parametrize('multi_trip', [False, True])
def test_ortools_fractional_shift_is_neither_seven_nor_eight_hours(multi_trip):
    from test_ortools_multitrip import ORToolsMultiTripTests, make_config, make_vehicle
    fixture = ORToolsMultiTripTests()
    # Two 10-minute legs + 410 minutes service = 430 minutes: >7h, <7.5h.
    vehicle = make_vehicle(max_time_hours=7.5, service_time_minutes=410)
    solver = fixture.make_solver(1, make_config(enable_multiple_trips=multi_trip), vehicle)
    solution = solver.solve()
    assert solution and solution.is_feasible and len(solution.routes) == 1
    # 460 minutes must not fit in the 450-minute shift (would fit in 8h).
    vehicle.service_time_minutes = 440
    solver = fixture.make_solver(1, make_config(enable_multiple_trips=multi_trip), vehicle)
    solution = solver.solve()
    assert not solution or not solution.is_feasible
