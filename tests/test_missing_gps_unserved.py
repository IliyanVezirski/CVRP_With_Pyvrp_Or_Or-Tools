"""Absent GPS orders survive input and reach unserved setData, never routing."""
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

import main
from config import MainConfig, VehicleConfig, VehicleType
from cvrp_solver import CVRPSolution, Route
from input_handler import InputHandler, Customer, customer_has_missing_gps
from pyvrp_next_runtime import validate_mandatory_customer_solution, MandatoryVisitInvariantError
from setdata_client import build_unserved_set_data_rows, upload_solution_set_data
from warehouse_manager import WarehouseManager


def config():
    cfg = MainConfig(vehicles=[VehicleConfig(
        vehicle_type=VehicleType.INTERNAL_BUS, name="Test", count=1, capacity=100,
    )])
    cfg.set_data.enable_set_data_upload = True
    cfg.set_data.enable_unserved_set_data_upload = True
    cfg.set_data.enable_make_group = False
    cfg.set_data.set_data_unserved_id_grafik = "UNSERVED"
    cfg.set_data.set_data_unserved_id_grafik_template = "{id_grafik}"
    cfg.set_data.set_data_unserved_bukva_template = "U-{stop_number}"
    cfg.set_data.set_data_missing_gps_id_grafik = "NO-GPS"
    cfg.set_data.set_data_missing_gps_bukva_template = "GPS-{stop_number}"
    return cfg


def record(doc="00000001", **changes):
    row = dict(IdCust="same-client", CustName="Test", GPS="", Volume=3,
               IdDoc=doc, IdPlasDoc=f"plas-{doc}", IdSkld="128")
    row.update(changes)
    return row


@pytest.mark.parametrize("gps", [None, "", "  \t", float("nan"), pd.NA])
def test_only_absent_gps_is_retained_for_unserved(gps):
    cfg = config()
    data = InputHandler(cfg).load_data_from_json_records([record(GPS=gps)])
    assert len(data.customers) == 1
    assert customer_has_missing_gps(data.customers[0])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert not allocation.vehicle_customers
    assert allocation.warehouse_customers == data.customers
    assert allocation.warehouse_volume == 3


def test_missing_gps_field_is_retained():
    row = record()
    del row["GPS"]
    data = InputHandler(config()).load_data_from_json_records([row])
    assert customer_has_missing_gps(data.customers[0])


@pytest.mark.parametrize("gps", ["invalid", "999,999", "0,0", "nan", "None", "42.7,23.3"])
def test_nonempty_values_are_not_reclassified_as_absent(gps):
    handler = InputHandler(config())
    customers = handler._process_json_records([record(GPS=gps)])
    assert not customer_has_missing_gps(customers[0])
    # Preserve the existing input policy for malformed, nonempty values.
    loaded = handler.load_data_from_json_records([record(GPS=gps)])
    assert bool(loaded.customers) == (customers[0].coordinates is not None)


def test_mixed_orders_keep_every_document_and_do_not_consume_vehicle_capacity():
    cfg = config()
    data = InputHandler(cfg).load_data_from_json_records([
        record("00000001", Volume=1000, Mandatory=True),
        record("00000002", Volume=2000),
        record("00000003", GPS="42.7,23.3", Volume=1),
    ])
    _, allocation = main.prepare_data(None, data, cfg)
    assert [c.document for c in allocation.vehicle_customers] == ["00000003"]
    assert [c.document for c in allocation.warehouse_customers] == ["00000001", "00000002"]
    assert allocation.total_vehicle_volume == 1
    assert allocation.warehouse_volume == 3000
    rows = build_unserved_set_data_rows(allocation.warehouse_customers, cfg)
    assert [r["IdPlasDoc"] for r in rows] == ["plas-00000001", "plas-00000002"]
    assert [r["Bukva"] for r in rows] == ["U-1", "U-2"]
    assert all(r["IdGrafik"] == "UNSERVED" and r["IdSkld"] == "128" for r in rows)
    solution = SimpleNamespace(routes=[SimpleNamespace(customers=allocation.vehicle_customers)], dropped_customers=[])
    validate_mandatory_customer_solution(allocation, solution)


@pytest.mark.parametrize("gps", [None, "", float("nan"), "absent-column"])
def test_excel_input_preserves_missing_gps(gps):
    cfg = config()
    cfg.input.input_source = "excel"
    row = {cfg.input.client_id_column: "C", cfg.input.client_name_column: "Name",
           cfg.input.volume_column: 1, cfg.input.document_column: "00001"}
    if gps != "absent-column":
        row[cfg.input.gps_column] = gps
    with patch("input_handler.os.path.exists", return_value=True), patch(
        "input_handler.pd.read_excel", return_value=pd.DataFrame([row])
    ):
        data = InputHandler(cfg).load_data("fake.xlsx")
    assert len(data.customers) == 1
    assert customer_has_missing_gps(data.customers[0])
    assert data.customers[0].document == "00001"


def test_http_getdata_preserves_absent_gps():
    cfg = config()
    cfg.input.input_source = "http_json"
    with patch("input_handler.urllib.request.urlopen", return_value=io.BytesIO(
        json.dumps([record(GPS=None)]).encode()
    )):
        data = InputHandler(cfg).load_data()
    assert customer_has_missing_gps(data.customers[0])


def test_all_missing_run_skips_matrix_and_solver_but_sends_unserved_orders():
    cfg = config()
    data = InputHandler(cfg).load_data_from_json_records([
        record(Mandatory=True), record("00000002", GPS=None),
    ])
    with patch.object(main, "setup_logging"), patch.object(main, "get_distance_matrix") as matrix, \
         patch.object(main, "solve_cvrp_worker") as solver, \
         patch.object(main, "process_results", return_value={}), \
         patch("setdata_client.send_set_data_row", return_value={"status_code": 200}) as send:
        result = main.run_optimization(input_data_override=data, config_override=cfg)
    matrix.assert_not_called()
    solver.assert_not_called()
    assert result["routes_count"] == 0
    assert result["customers_total"] == 2
    assert result["customers_for_warehouse"] == 2
    assert result["set_data"]["unserved_attempted"] == 0
    assert result["set_data"]["missing_gps_attempted"] == 2
    sent = [call.args[0] for call in send.call_args_list]
    assert {r["IdPlasDoc"] for r in sent} == {"plas-00000001", "plas-00000002"}
    assert all(r["IdGrafik"] == "NO-GPS" and r["DoneFlag"] == "0" for r in sent)


def test_upload_disabled_remains_disabled():
    cfg = config()
    cfg.set_data.enable_set_data_upload = False
    data = InputHandler(cfg).load_data_from_json_records([record()])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    with patch("setdata_client.send_set_data_row") as send:
        result = upload_solution_set_data(SimpleNamespace(routes=[], dropped_customers=[]), cfg, allocation)
    assert result["enabled"] is False
    send.assert_not_called()


def test_mixed_run_passes_only_located_customers_to_matrix_and_solver():
    cfg = config()
    cfg.cvrp.enable_parallel_solving = False
    data = InputHandler(cfg).load_data_from_json_records([
        record("missing", Mandatory=True), record("located", GPS="42.7,23.3"),
    ])

    def matrix_for_located(allocation, *args):
        assert [c.document for c in allocation.vehicle_customers] == ["located"]
        return object()

    def solve_located(args):
        allocation = args[0]
        assert [c.document for c in allocation.vehicle_customers] == ["located"]
        route = Route(VehicleType.INTERNAL_BUS, 0, allocation.vehicle_customers,
                      cfg.locations.depot_location, total_volume=3)
        return CVRPSolution([route], [], 1, 1, 1, 1, True)

    with patch.object(main, "setup_logging"), \
         patch.object(main, "get_distance_matrix", side_effect=matrix_for_located) as matrix, \
         patch.object(main, "solve_cvrp_worker", side_effect=solve_located) as solver, \
         patch.object(main, "process_results", return_value={}), \
         patch("setdata_client.send_set_data_row", return_value={"status_code": 200}) as send:
        result = main.run_optimization(input_data_override=data, config_override=cfg)
    matrix.assert_called_once()
    solver.assert_called_once()
    assert result["routes_count"] == 1
    assert result["set_data"]["unserved_attempted"] == 0
    assert result["set_data"]["missing_gps_attempted"] == 1
    rows = [call.args[0] for call in send.call_args_list]
    missing = [r for r in rows if r.get("IdPlasDoc") == "plas-missing"]
    assert len(missing) == 1
    assert missing[0]["IdGrafik"] == "NO-GPS" and missing[0]["DoneFlag"] == "0"
    assert any(r.get("IdPlasDoc") == "plas-located" for r in rows)


def test_malformed_mandatory_warehouse_order_still_rejected():
    client = Customer("C", "Name", None, 1, "invalid", mandatory=True)
    with pytest.raises(MandatoryVisitInvariantError):
        validate_mandatory_customer_solution(
            SimpleNamespace(vehicle_customers=[], warehouse_customers=[client]),
            SimpleNamespace(routes=[], dropped_customers=[]),
        )
