from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import cvrp_api_server as api
from input_handler import InputHandler
from output_handler import ExcelExporter
from setdata_client import build_unserved_set_data_rows, upload_solution_set_data
from warehouse_manager import WarehouseManager
from tests.test_missing_gps_unserved import config, record


def setup(gps='42.7,23.3'):
    cfg = config()
    cfg.input.excluded_customer_ids = 'same-client'
    cfg.set_data.set_data_excluded_id_grafik = '000987'
    cfg.set_data.set_data_unserved_id_grafik_template = 'OLD-{id_grafik}'
    data = InputHandler(cfg).load_data_from_json_records([
        record('excluded-1', GPS=gps), record('excluded-2', GPS=gps),
        record('ordinary', IdCust='another-client', GPS='42.71,23.31'),
    ])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    return cfg, allocation


def test_only_manual_exclusions_override_direction_and_keep_document_ids():
    cfg, allocation = setup()
    original = deepcopy(cfg.set_data)
    customers = allocation.vehicle_customers + allocation.warehouse_customers
    rows = build_unserved_set_data_rows(customers, cfg)
    assert rows[0]['IdGrafik'] == 'OLD-UNSERVED'
    assert [r['IdGrafik'] for r in rows[1:]] == ['000987', '000987']
    assert [r['IdPlasDoc'] for r in rows[1:]] == ['plas-excluded-1', 'plas-excluded-2']
    assert cfg.set_data == original
    report = ExcelExporter(cfg.output, cfg.set_data)._unserved_report_records(customers)
    excluded = [r for r in report if r['ID'] == 'same-client']
    assert len(excluded) == 2
    assert [r['Направление'] for r in excluded] == ['000987', '000987']
    assert [r['Буква'] for r in excluded] == [r['Bukva'] for r in rows[1:]]
    assert sum(r[next(k for k in r if k.startswith('Обем'))] for r in excluded) == 6


def test_final_update_does_not_replace_manual_direction():
    cfg, allocation = setup()
    cfg.set_data.enable_make_group = True
    cfg.set_data.enable_unserved_final_done_flag_update = True
    cfg.set_data.set_data_unserved_done_flag = '0'
    cfg.set_data.set_data_unserved_final_done_flag = '1973'
    allocation.warehouse_customers = allocation.vehicle_customers + allocation.warehouse_customers
    with patch('setdata_client.send_set_data_row', return_value={'status_code': 200}) as send:
        upload_solution_set_data(SimpleNamespace(routes=[], dropped_customers=[]), cfg, allocation)
    requests = [c.args[0] for c in send.call_args_list]
    for doc in ['plas-excluded-1', 'plas-excluded-2']:
        rows = [r for r in requests if r.get('IdPlasDoc') == doc]
        assert [r['DoneFlag'] for r in rows] == ['0', '1973']
        assert all(r['IdGrafik'] == '000987' for r in rows)
    assert all(r['IdGrafik'] == 'OLD-UNSERVED' for r in requests if r.get('IdPlasDoc') == 'plas-ordinary')


def test_missing_gps_keeps_direct_flag_but_uses_explicit_manual_direction():
    cfg, allocation = setup(gps='')
    report = ExcelExporter(cfg.output, cfg.set_data)._unserved_report_records(allocation.warehouse_customers)
    assert all(r['Направление'] == '000987' and r['DoneFlag'] == '0' for r in report)
    with patch('setdata_client.send_set_data_row', return_value={'status_code': 200}) as send:
        upload_solution_set_data(SimpleNamespace(routes=[], dropped_customers=[]), cfg, allocation)
    assert send.call_count == 2
    assert all(c.args[0]['IdGrafik'] == '000987' and c.args[0]['DoneFlag'] == '0' for c in send.call_args_list)


def test_blank_field_preserves_old_direction():
    cfg, allocation = setup()
    cfg.set_data.set_data_excluded_id_grafik = ''
    assert all(r['IdGrafik'] == 'OLD-UNSERVED' for r in build_unserved_set_data_rows(allocation.warehouse_customers, cfg))


def test_web_and_legacy_persistence(tmp_path):
    cfg = config()
    cfg.set_data = SimpleNamespace(**{k: v for k, v in vars(cfg.set_data).items() if k != 'set_data_excluded_id_grafik'})
    assert api._shared_exclusions(cfg)['direction'] == ''
    with patch.object(api, 'get_config', return_value=cfg):
        changed, applied, ignored = api._build_web_run_config_override({'run_settings': {'set_data': {'set_data_excluded_id_grafik': '000987'}}})
    assert not ignored
    assert api._shared_exclusions(changed)['direction'] == ''
    changed.set_data.set_data_excluded_id_grafik = '000987'
    assert not hasattr(cfg.set_data, 'set_data_excluded_id_grafik')
    source = 'from dataclasses import dataclass\n@dataclass\nclass SetDataConfig:\n    enable_set_data_upload: bool = False\n'
    path = tmp_path / 'config.py'
    path.write_text(source, encoding='utf-8')
    with patch.object(api.config_module, '__file__', str(path)):
        api._persist_web_gui_config(changed, include_api=False, include_cvrp=False,
                                    include_vehicles=False, include_set_data=True)
    namespace = {}
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), namespace)
    assert namespace['SetDataConfig']().set_data_excluded_id_grafik == '000987'
    page = api._web_gui_html(cfg.api, '127.0.0.1', 18095)
    assert 'id="setDataExcludedGrafik"' in page
    assert "put('setDataExcludedGrafik',saved.direction" in page
    assert "direction:value('setDataExcludedGrafik')" in page
