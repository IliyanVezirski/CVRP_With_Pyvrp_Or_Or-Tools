"""Document exclusions are exact, shared, and independent of other orders."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import cvrp_api_server as api
from config_compat import compile_external_config
from input_handler import InputHandler
from output_handler import ExcelExporter
from pyvrp_next_runtime import validate_mandatory_customer_solution, MandatoryVisitInvariantError
from setdata_client import upload_solution_set_data
from warehouse_manager import WarehouseManager
from tests.test_missing_gps_unserved import config, record


@pytest.mark.parametrize('grouped', [True, False])
def test_exact_document_exclusion_before_grouping(grouped):
    cfg = config()
    cfg.input.excluded_document_ids = '00001,00001'
    cfg.input.enable_customer_document_grouping = grouped
    data = InputHandler(cfg).load_data_from_json_records([
        record('00001', GPS='42.7,23.3', Volume=10000, Mandatory=True),
        record('1', GPS='42.7,23.3', Volume=2, Mandatory=True),
        record('00002', GPS='42.7,23.3', Volume=3),
    ])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert allocation.warehouse_volume == 10000
    assert allocation.total_vehicle_volume == 5
    assert [c.document for c in allocation.warehouse_customers] == ['00001']
    assert sum(len(c.grouped_documents) for c in allocation.vehicle_customers) == 2
    solution = SimpleNamespace(routes=[SimpleNamespace(customers=allocation.vehicle_customers)], dropped_customers=[])
    validate_mandatory_customer_solution(allocation, solution)
    forbidden = deepcopy(allocation.warehouse_customers[0])
    forbidden.manually_excluded = False
    solution.routes[0].customers.append(forbidden)
    with pytest.raises(MandatoryVisitInvariantError):
        validate_mandatory_customer_solution(allocation, solution)


def test_pre_grouped_input_is_partitioned_without_losing_volume():
    cfg = config()
    data = InputHandler(cfg).load_data_from_json_records([
        record('00001', GPS='42.7,23.3', Volume=40, Mandatory=True),
        record('00002', GPS='42.7,23.3', Volume=2),
    ])
    assert len(data.customers) == 1
    cfg.input.excluded_document_ids = '00001'
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert allocation.total_vehicle_volume == 2
    assert allocation.warehouse_volume == 40
    assert not allocation.vehicle_customers[0].mandatory
    cfg.input.excluded_document_ids = ''
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert not allocation.warehouse_customers
    assert allocation.total_vehicle_volume == 42


@pytest.mark.parametrize('missing_gps', [False, True])
@pytest.mark.parametrize('exclude_by', ['customer', 'document'])
def test_send_gate_applies_to_all_paths_but_keeps_report(missing_gps, exclude_by):
    cfg = config()
    cfg.input.excluded_document_ids = '00001' if exclude_by == 'document' else ''
    cfg.input.excluded_customer_ids = 'same-client' if exclude_by == 'customer' else ''
    cfg.set_data.enable_excluded_set_data_upload = False
    cfg.set_data.enable_make_group = True
    data = InputHandler(cfg).load_data_from_json_records([
        record('00001', GPS='' if missing_gps else '42.7,23.3'),
        record('other', IdCust='other', GPS='42.7,23.3'),
    ])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    solution = SimpleNamespace(routes=[], dropped_customers=allocation.vehicle_customers)
    original_flags = (cfg.set_data.set_data_done_flag, cfg.set_data.set_data_unserved_done_flag)
    with patch('setdata_client.send_set_data_row', return_value={'status_code':200}) as send:
        upload_solution_set_data(solution, cfg, allocation)
    sent = [c.args[0] for c in send.call_args_list]
    assert not any(r.get('IdPlasDoc') == 'plas-00001' for r in sent)
    assert any(r.get('IdPlasDoc') == 'plas-other' for r in sent)
    report = ExcelExporter(cfg.output, cfg.set_data)._unserved_report_records(allocation.warehouse_customers)
    assert len(report) == 1 and 'Ръчно изключен' in report[0]['Коментар']
    assert original_flags == (cfg.set_data.set_data_done_flag, cfg.set_data.set_data_unserved_done_flag)


def test_shared_save_preserves_unrelated_settings_and_old_client_payload():
    cfg = config()
    before = deepcopy(cfg)
    manager = SimpleNamespace(config=cfg)
    with patch.object(api, 'get_config', side_effect=lambda: manager.config), \
         patch.object(api.config_module, 'config_manager', manager), \
         patch.object(api, '_persist_web_gui_config', return_value='backup'):
        api._save_shared_exclusions({'customer_ids':'001', 'document_ids':'00001, 00002',
                                    'direction':'123', 'send_enabled':False})
        api._save_shared_exclusions({'customer_ids':'002', 'direction':'124'})
        web = api._build_web_run_config_override({'run_settings': {
            'input': {'excluded_document_ids':'OTHER'},
            'set_data': {'enable_excluded_set_data_upload': True}}})[0]
    assert cfg.input.excluded_document_ids == '00001\n00002'
    assert web.input.excluded_document_ids == '00001\n00002'
    assert cfg.set_data.enable_excluded_set_data_upload is False
    assert web.set_data.enable_excluded_set_data_upload is False
    assert cfg.set_data.set_data_done_flag == before.set_data.set_data_done_flag
    assert cfg.set_data.set_data_unserved_done_flag == before.set_data.set_data_unserved_done_flag


def test_legacy_schema_and_persistence():
    source = ('from dataclasses import dataclass\n@dataclass\nclass InputConfig:\n'
              '    input_source: str = "excel"\n\n@dataclass\nclass SetDataConfig:\n'
              '    enable_set_data_upload: bool = False\n')
    scope = {}
    exec(compile_external_config(source, 'legacy.py'), scope)
    assert scope['InputConfig']().excluded_document_ids == ''
    assert scope['SetDataConfig']().enable_excluded_set_data_upload is True
    saved = api._replace_class_field_literal(source, 'InputConfig', 'excluded_document_ids', '00001')
    saved = api._replace_class_field_literal(saved, 'SetDataConfig', 'enable_excluded_set_data_upload', False)
    exec(compile(saved, 'saved.py', 'exec'), scope)
    assert scope['InputConfig']().excluded_document_ids == '00001'
    assert scope['SetDataConfig']().enable_excluded_set_data_upload is False
