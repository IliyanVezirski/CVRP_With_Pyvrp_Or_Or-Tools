"""Quick service controls and reload-depot selections preserve saved values."""
from copy import deepcopy

import cvrp_api_server as api
from config import get_config
from supervisor_ui import supervisor_script


def test_reload_depot_none_remains_follow_start_when_displayed_and_saved():
    cfg = deepcopy(get_config())
    vehicle = cfg.vehicles[0]
    vehicle.reload_location = None
    assert api._vehicle_to_web_dict(vehicle, cfg)["reload_location"] == ""
    assert "reload_location=None," in api._vehicle_config_literal(vehicle)
    changed = api._vehicle_from_payload(cfg, {"reload_location": None}, vehicle)
    assert changed.reload_location is None


def test_reload_depot_coordinates_round_trip_without_changing_service():
    cfg = deepcopy(get_config())
    vehicle = cfg.vehicles[0]
    vehicle.reload_location = (43.221, 23.534)
    data = api._vehicle_to_web_dict(vehicle, cfg)
    changed = api._vehicle_from_payload(cfg, {"reload_location": data["reload_location"]}, vehicle)
    assert changed.reload_location == vehicle.reload_location
    assert changed.service_time_minutes == vehicle.service_time_minutes


def test_service_controls_are_outside_vehicle_details():
    script = supervisor_script()
    assert "serviceControl.append(modeField,fixedField)" in script
    assert "core.after(formulaDetails)" in script
    assert "formulaDetails.open=true" not in script
    assert "extraFields.prepend(formulaBox)" not in script
    assert "serviceMode.addEventListener('change',refreshService)" in script


def test_reload_depot_is_select_and_keeps_unlisted_saved_location():
    cfg = get_config()
    html = api._web_gui_html(cfg.api, "127.0.0.1", 18765)
    assert '<select data-field="reload_location"></select>' in html
    assert "Като началното депо" in html
    assert "Запазено място (извън списъка)" in html
    assert 'get("reload_location").value || null' in html
