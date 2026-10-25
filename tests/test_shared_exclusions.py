import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import cvrp_api_server as api
from input_handler import InputHandler, shared_customer_exclusions
from warehouse_manager import WarehouseManager
from tests.test_missing_gps_unserved import config, record


def test_legacy_lists_are_combined_for_both_profiles_and_direct_run():
    cfg = config()
    cfg.input.excluded_customer_ids = 'A'
    cfg.set_data.set_data_excluded_id_grafik = '123'
    cfg.api.web_gui_run_defaults_json = json.dumps({'version':2, 'settings':{
        'input':{'excluded_customer_ids':'B,A'}, 'set_data':{'set_data_excluded_id_grafik':'456'}}})
    before = deepcopy(cfg)
    assert shared_customer_exclusions(cfg) == {'customer_ids':'A\nB', 'direction':'123', 'direction_conflict':True}
    for profile in ['web', 'global']:
        with patch.object(api, 'get_config', return_value=cfg):
            effective = api._build_web_run_config_override({'profile':profile, 'run_settings':{
                'input':{'excluded_customer_ids':'WRONG'}, 'set_data':{'set_data_excluded_id_grafik':'WRONG'}}})[0]
        assert shared_customer_exclusions(effective) == shared_customer_exclusions(cfg)
    data = InputHandler(cfg).load_data_from_json_records([
        record('01',IdCust='A',GPS='42.7,23.3'),record('02',IdCust='B',GPS='42.7,23.3'),
        record('03',IdCust='C',GPS='42.7,23.3')])
    allocation = WarehouseManager(main_config=cfg).allocate_customers(data)
    assert {c.id for c in allocation.warehouse_customers} == {'A','B'}
    assert [c.id for c in allocation.vehicle_customers] == ['C']
    assert cfg == before


def test_shared_save_only_changes_common_fields_and_removes_legacy_entries():
    cfg = config()
    cfg.api.web_gui_run_defaults_json = json.dumps({'version':2,'settings':{
        'input':{'excluded_customer_ids':'OLD','json_sklad':'999'},
        'set_data':{'set_data_excluded_id_grafik':'OLD','enable_set_data_upload':False}}})
    fleet = deepcopy(cfg.vehicles)
    solver = deepcopy(cfg.cvrp)
    manager = SimpleNamespace(config=cfg)
    with patch.object(api, 'get_config', return_value=cfg), patch.object(api.config_module, 'config_manager', manager), \
         patch.object(api, '_persist_web_gui_config', return_value='backup') as persist:
        result = api._save_shared_exclusions({'customer_ids':'001,002,001','direction':'00099'})
    assert result['scope'] == 'shared_customer_exclusions'
    assert cfg.input.excluded_customer_ids == '001\n002'
    assert cfg.set_data.set_data_excluded_id_grafik == '00099'
    assert cfg.vehicles == fleet and cfg.cvrp == solver
    assert json.loads(cfg.api.web_gui_run_defaults_json)['settings'] == {
        'input':{'json_sklad':'999'},'set_data':{'enable_set_data_upload':False}}
    assert persist.call_args.kwargs == {'include_api':False,'include_cvrp':False,'include_vehicles':False,'include_exclusions':True}
    with patch.object(api, 'get_config', return_value=cfg), patch.object(api.config_module, 'config_manager', manager), \
         patch.object(api, '_persist_web_gui_config', return_value='backup'):
        api._save_shared_exclusions({'customer_ids':'','direction':''})
    assert shared_customer_exclusions(cfg)['customer_ids'] == ''


def test_shared_save_is_atomic_on_disk_failure():
    cfg = config()
    before = deepcopy(cfg)
    with patch.object(api, 'get_config', return_value=cfg), \
         patch.object(api, '_persist_web_gui_config', side_effect=OSError('full')):
        with pytest.raises(OSError):
            api._save_shared_exclusions({'customer_ids':'123','direction':'456'})
    assert cfg == before


def test_profile_reset_preserves_legacy_common_list():
    cfg = config()
    cfg.api.web_gui_run_defaults_json = json.dumps({'version':2,'settings':{
        'input':{'excluded_customer_ids':'001'},'set_data':{'set_data_excluded_id_grafik':'008'}}})
    manager = SimpleNamespace(config=cfg)
    with patch.object(api, 'get_config', side_effect=lambda:manager.config), \
         patch.object(api.config_module, 'config_manager', manager), \
         patch.object(api, '_persist_web_gui_config', return_value='backup') as persist:
        api._save_web_run_defaults({'clear':True})
    assert shared_customer_exclusions(manager.config)['customer_ids'] == '001'
    assert shared_customer_exclusions(manager.config)['direction'] == '008'
    assert persist.call_args.kwargs['include_exclusions'] is True


def test_report_uses_legacy_shared_direction_before_first_save():
    from output_handler import ExcelExporter
    from setdata_client import build_unserved_set_data_rows
    cfg=config()
    cfg.input.excluded_customer_ids='same-client'
    cfg.api.web_gui_run_defaults_json=json.dumps({'version':2,'settings':{
        'set_data':{'set_data_excluded_id_grafik':'000888'}}})
    data=InputHandler(cfg).load_data_from_json_records([record(GPS='42.7,23.3')])
    allocation=WarehouseManager(main_config=cfg).allocate_customers(data)
    with patch('output_handler.get_config',return_value=cfg):
        report=ExcelExporter(cfg.output)._unserved_report_records(allocation.warehouse_customers)
    rows=build_unserved_set_data_rows(allocation.warehouse_customers,cfg)
    assert report[0]['Направление']==rows[0]['IdGrafik']=='000888'


def test_dialog_is_outside_profiles_and_button_is_in_header():
    page = api._web_gui_html(config().api, '127.0.0.1',18095)
    assert page.index('id="excludedCustomersButton"') < page.index('</header>')
    assert page.index('id="exclusionsDialog"') < page.index('id="runSettingsSection"')
    assert page.count('id="inExcludedCustomers"') == 1
    assert page.count('id="setDataExcludedGrafik"') == 1
    assert "apiFetch('/customer-exclusions'" in page
    script = page.split('async function saveExclusions()',1)[1].split('const vehicleNames',1)[0]
    assert 'renderConfig(' not in script and 'renderVehicles(' not in script and 'renderRunSettings(' not in script


def test_dialog_save_cancel_and_error_interactions():
    from web_portal import portal_script
    script = 'let exclusionsBusy=false;' + portal_script().split('let exclusionsBusy=false;',1)[1].split('const vehicleNames',1)[0]
    harness = r'''
const assert=require('node:assert/strict');
const fs=require('node:fs');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const elements={};
const $=id=>elements[id] ||= {value:'',textContent:'',disabled:false,open:false,
  showModal(){this.open=true;},close(){this.open=false;}};
const state={customer_exclusions:{customer_ids:'001\n002',direction:'0009'},vehicles:['draft'],web_run_settings:{draft:true}};
let fail=false;
let calls=[];
const fetch=async(url,options)=>{calls.push(JSON.parse(options.body));if(fail)throw Error('Неуспешен запис');
  return {config:{customer_exclusions:{customer_ids:'003',direction:'0008'},revision:'new'}};};
const controls=new Function('$','state','put','value','apiFetch',script+';return {openExclusions,closeExclusions,saveExclusions,updateExclusionButton};')
 ($,state,(id,v)=>$(id).value=v,id=>$(id).value,fetch);
(async()=>{
 controls.openExclusions();assert.equal($('exclusionsDialog').open,true);
 $('inExcludedCustomers').value='discard';controls.closeExclusions();
 assert.equal(calls.length,0);controls.openExclusions();assert.equal($('inExcludedCustomers').value,'001\n002');
 $('inExcludedCustomers').value='003';$('setDataExcludedGrafik').value='0008';await controls.saveExclusions();
 assert.deepEqual(calls[0],{customer_ids:'003',direction:'0008'});
 assert.equal($('excludedCustomersCount').textContent,'· 1');assert.equal($('exclusionsDialog').open,false);
 assert.deepEqual(state.vehicles,['draft']);assert.deepEqual(state.web_run_settings,{draft:true});
 controls.openExclusions();fail=true;await controls.saveExclusions();assert.equal($('exclusionsDialog').open,true);
 assert.equal($('exclusionsStatus').textContent,'Неуспешен запис');assert.equal($('exclusionsSave').disabled,false);
})().catch(err=>{console.error(err);process.exit(1)});
'''
    result = subprocess.run([r'C:\Program Files\nodejs\node.exe','-e',harness],
        input=json.dumps(script),text=True,capture_output=True,encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_only_common_fields_are_written(tmp_path):
    cfg = config()
    cfg.input.excluded_customer_ids='001'
    cfg.set_data.set_data_excluded_id_grafik='0009'
    cfg.api.web_gui_run_defaults_json=''
    source = '''from dataclasses import dataclass
@dataclass
class InputConfig:
    input_source: str = "keep-input"
@dataclass
class SetDataConfig:
    enable_set_data_upload: bool = False
    set_data_unserved_id_grafik: str = "keep-direction"
@dataclass
class APIConfig:
    web_gui_run_defaults_json: str = "old"
    api_port: int = 9876
'''
    path=tmp_path/'config.py'
    path.write_text(source,encoding='utf-8')
    with patch.object(api.config_module,'__file__',str(path)):
        api._persist_web_gui_config(cfg,include_api=False,include_cvrp=False,include_vehicles=False,include_exclusions=True)
    scope={}
    exec(path.read_text(encoding='utf-8'),scope)
    assert scope['InputConfig']().excluded_customer_ids=='001'
    assert scope['InputConfig']().input_source=='keep-input'
    assert scope['SetDataConfig']().set_data_excluded_id_grafik=='0009'
    assert scope['SetDataConfig']().set_data_unserved_id_grafik=='keep-direction'
    assert scope['APIConfig']().api_port==9876
