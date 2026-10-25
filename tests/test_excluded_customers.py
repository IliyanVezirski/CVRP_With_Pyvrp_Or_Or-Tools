import io
import json
import types
import re
import subprocess
from unittest.mock import patch

import pytest

import main
import cvrp_api_server as api
from config_compat import compile_external_config
from cvrp_solver import CVRPSolution, Route
from input_handler import InputHandler, normalise_excluded_customer_ids
from output_handler import ExcelExporter
from pyvrp_next_runtime import validate_mandatory_customer_solution, MandatoryVisitInvariantError
from setdata_client import build_unserved_set_data_rows
from warehouse_manager import WarehouseManager
from tests.test_missing_gps_unserved import config, record


def test_ids_keep_zeroes_and_match_exactly():
    assert normalise_excluded_customer_ids('001, 12;001\n123\tABC') == '001\n12\n123\nABC'
    with pytest.raises(ValueError):
        normalise_excluded_customer_ids(123)
    cfg = config()
    cfg.input.excluded_customer_ids = '001,12'
    data = InputHandler(cfg).load_data_from_json_records([
        record(str(i), IdCust=value, GPS='42.7,23.3')
        for i, value in enumerate(['001', '1', '12', '123'])
    ])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert {c.id for c in allocation.warehouse_customers} == {'001', '12'}
    assert {c.id for c in allocation.vehicle_customers} == {'1', '123'}
    cfg.input.excluded_customer_ids = ''
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert len(allocation.vehicle_customers) == 4


@pytest.mark.parametrize('grouped', [True, False])
def test_all_customer_documents_excluded_before_capacity_and_mandatory_checks(grouped):
    cfg = config()
    cfg.input.excluded_customer_ids = 'same-client'
    cfg.input.enable_customer_document_grouping = grouped
    data = InputHandler(cfg).load_data_from_json_records([
        record('00001', GPS='42.7,23.3', Volume=10000, Mandatory=True),
        record('00002', GPS='42.7,23.3', Volume=10000),
        record('00003', GPS='42.71,23.31', IdCust='allowed', Volume=1),
    ])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert [c.id for c in allocation.vehicle_customers] == ['allowed']
    assert allocation.total_vehicle_volume == 1
    assert allocation.warehouse_volume == 20000
    assert all(c.manually_excluded for c in allocation.warehouse_customers)
    rows = build_unserved_set_data_rows(allocation.warehouse_customers, cfg)
    assert {r['IdPlasDoc'] for r in rows} == {'plas-00001', 'plas-00002'}
    assert all(r['IdGrafik'] == 'UNSERVED' for r in rows)
    solution = types.SimpleNamespace(routes=[types.SimpleNamespace(customers=allocation.vehicle_customers)], dropped_customers=[])
    validate_mandatory_customer_solution(allocation, solution)
    solution.routes[0].customers += allocation.warehouse_customers
    with pytest.raises(MandatoryVisitInvariantError):
        validate_mandatory_customer_solution(allocation, solution)


def test_all_excluded_skips_matrix_and_solver_and_uses_unserved_settings():
    cfg = config()
    cfg.input.excluded_customer_ids = 'same-client'
    cfg.set_data.enable_unserved_final_done_flag_update = False
    cfg.set_data.set_data_unserved_done_flag = '77'
    data = InputHandler(cfg).load_data_from_json_records([record(GPS='42.7,23.3', Mandatory=True)])
    with patch.object(main, 'setup_logging'), patch.object(main, 'get_distance_matrix') as matrix, \
         patch.object(main, 'solve_cvrp_worker') as solver, patch.object(main, 'process_results', return_value={}), \
         patch('setdata_client.send_set_data_row', return_value={'status_code': 200}) as send:
        result = main.run_optimization(input_data_override=data, config_override=cfg)
    matrix.assert_not_called()
    solver.assert_not_called()
    assert result['routes_count'] == 0
    assert result['customers_for_warehouse'] == 1
    assert send.call_count == 1
    assert send.call_args.args[0]['IdGrafik'] == 'UNSERVED'
    assert send.call_args.args[0]['DoneFlag'] == '77'


def test_mixed_run_sends_only_allowed_customer_to_matrix_and_solver():
    cfg = config()
    cfg.input.excluded_customer_ids = 'same-client'
    cfg.cvrp.enable_parallel_solving = False
    cfg.set_data.enable_set_data_upload = False
    data = InputHandler(cfg).load_data_from_json_records([
        record('excluded', GPS='42.7,23.3'),
        record('allowed', IdCust='other', GPS='42.71,23.31'),
    ])
    def solve(args):
        allocation = args[0]
        assert [c.id for c in allocation.vehicle_customers] == ['other']
        route = Route(cfg.vehicles[0].vehicle_type, 0, allocation.vehicle_customers,
                      cfg.locations.depot_location, total_volume=3)
        return CVRPSolution([route], [], 1, 1, 1, 1, True)
    def matrix(allocation, *args):
        assert [c.id for c in allocation.vehicle_customers] == ['other']
        return object()
    with patch.object(main, 'setup_logging'), patch.object(main, 'get_distance_matrix', side_effect=matrix), \
         patch.object(main, 'solve_cvrp_worker', side_effect=solve), \
         patch.object(main, 'process_results', return_value={}), patch('setdata_client.send_set_data_row') as send:
        result = main.run_optimization(input_data_override=data, config_override=cfg)
    assert result['customers_for_warehouse'] == 1
    assert result['routes_count'] == 1
    send.assert_not_called()


def test_excel_comment_and_original_comment_preserved():
    cfg = config()
    cfg.input.excluded_customer_ids = 'same-client'
    data = InputHandler(cfg).load_data_from_json_records([record(GPS='42.7,23.3', DeliveryComment='Original')])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    records = ExcelExporter(cfg.output, cfg.set_data)._unserved_report_records(allocation.warehouse_customers)
    assert records[0]['Коментар'] == 'Ръчно изключен от маршрутите; Original'
    assert data.customers[0].delivery_comment == 'Original'


def test_web_profile_and_global_field_and_legacy_schema():
    cfg = config()
    with patch.object(api, 'get_config', return_value=cfg):
        changed, applied, ignored = api._build_web_run_config_override({'run_settings': {'input': {'excluded_customer_ids': '001,002'}}})
    assert not ignored
    assert changed.input.excluded_customer_ids == ''  # Profile overrides no longer change shared lists.
    assert cfg.input.excluded_customer_ids == ''
    assert 'excluded_customer_ids' not in api._web_run_settings_from_config(changed)['input']
    source = 'from dataclasses import dataclass\n@dataclass\nclass InputConfig:\n    input_source: str = "excel"\n'
    scope = {}
    exec(compile_external_config(source, 'legacy.py'), scope)
    assert scope['InputConfig']().excluded_customer_ids == ''
    saved = api._replace_class_field_literal(source, 'InputConfig', 'excluded_customer_ids', '001\n002')
    exec(compile(saved, 'saved.py', 'exec'), scope)
    assert scope['InputConfig']().excluded_customer_ids == '001\n002'
    page = api._web_gui_html(cfg.api, '127.0.0.1', 18095)
    assert 'id="inExcludedCustomers"' in page
    assert "inExcludedCustomers:'excluded_customer_ids'" not in page
    assert 'id="exclusionsDialog"' in page


def test_http_input_excludes_mandatory_with_malformed_gps_without_losing_order():
    cfg = config()
    cfg.input.input_source = 'http_json'
    cfg.input.excluded_customer_ids = 'same-client'
    with patch('input_handler.urllib.request.urlopen', return_value=io.BytesIO(
        json.dumps([record(GPS='invalid', Mandatory=True)]).encode()
    )):
        data = InputHandler(cfg).load_data()
    assert len(data.customers) == 1
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert not allocation.vehicle_customers
    assert allocation.warehouse_customers[0].manually_excluded


def test_missing_gps_and_manual_exclusion_is_one_unserved_visit():
    cfg = config()
    cfg.input.excluded_customer_ids = 'same-client'
    data = InputHandler(cfg).load_data_from_json_records([record()])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert len(allocation.warehouse_customers) == 1
    rows = ExcelExporter(cfg.output, cfg.set_data)._unserved_report_records(allocation.warehouse_customers)
    assert len(rows) == 1
    assert 'Без GPS' in rows[0]['Коментар']
    assert 'Ръчно изключен' in rows[0]['Коментар']


def test_global_save_actually_persists_to_legacy_file(tmp_path):
    cfg = config()
    cfg.input.excluded_customer_ids = '001\n002'
    path = tmp_path / 'config.py'
    path.write_text('from dataclasses import dataclass\n@dataclass\nclass InputConfig:\n    input_source: str = "excel"\n', encoding='utf-8')
    with patch.object(api.config_module, '__file__', str(path)):
        api._persist_web_gui_config(cfg, include_input=True, include_api=False,
                                    include_cvrp=False, include_vehicles=False)
    scope = {}
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), scope)
    assert scope['InputConfig']().excluded_customer_ids == '001\n002'


def test_profile_save_and_overrides_cannot_change_shared_exclusions():
    cfg = config()
    cfg.input.excluded_customer_ids = 'GLOBAL'
    manager = types.SimpleNamespace(config=cfg)
    with patch.object(api, 'get_config', side_effect=lambda: manager.config), \
         patch.object(api.config_module, 'config_manager', manager), \
         patch.object(api, '_persist_web_gui_config', return_value='backup'):
        api._save_web_run_defaults({'run_settings': {'input': {'excluded_customer_ids': '001,002'}}})
        web = api._build_web_run_config_override({})[0]
        base = api._build_web_run_config_override({'profile': 'global'})[0]
        cleared = api._build_web_run_config_override({'run_settings': {'input': {'excluded_customer_ids': ''}}})[0]
    assert web.input.excluded_customer_ids == 'GLOBAL'
    assert base.input.excluded_customer_ids == 'GLOBAL'
    assert cleared.input.excluded_customer_ids == 'GLOBAL'


def test_web_javascript_parses():
    page = api._web_gui_html(config().api, '127.0.0.1', 18095)
    script = re.search(r'<script>(.*?)</script>', page, re.S)[1]
    result = subprocess.run([r'C:\Program Files\nodejs\node.exe', '--check', '-'],
                            input=script, text=True, encoding='utf-8', capture_output=True)
    assert result.returncode == 0, result.stderr
