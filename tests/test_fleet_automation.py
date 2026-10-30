from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import fleet_automation as fleet
import operations_web
from config import MainConfig, VehicleConfig, VehicleType
from input_handler import InputHandler
from service_time import customer_service_seconds


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, 'store_path', lambda:tmp_path/'fleet.json')
    cfg = MainConfig(vehicles=[VehicleConfig(VehicleType.INTERNAL_BUS,100,2,config_id='van',name='Маршрут')])
    cfg.api.web_gui_run_defaults_json=''
    cfg.input.json_override_date='02/11/2026'
    cfg.input.input_source='http_json'
    cfg.input.excluded_customer_ids=''
    cfg.set_data.set_data_id_skld='106'
    cfg.set_data.set_data_vratza_id_skld='128'
    return cfg, fleet.defaults()


def fixed(seconds): return {'mode':'fixed','seconds':seconds}
def rule(low=0, high=None, seconds=300): return {'min':low,'max':high,'service':fixed(seconds)}
def entry(kind='weekly',count=1,service=None):
    return dict(id=kind,name=kind,enabled=True,kind=kind,date='2026-11-02',weekdays=[0],
                vehicles={'van':{'count':count,'enabled':True,'service':service or {'mode':'inherit'}}})


def test_calendar_rejects_counts_above_fleet_and_accepts_blank_inherited_limits(setup):
    cfg,settings=setup
    row=entry(count=3)
    body={'section':'calendar','revision':fleet.revision(settings),'value':{'enabled':True,'entries':[row]}}
    with pytest.raises(ValueError,match='наличните'):fleet.save(body,cfg)
    row['vehicles']['van'].update(count=1,capacity=None,max_time_hours=7.5)
    fleet.save(body,cfg)
    saved=fleet.read()['calendar']['entries'][0]['vehicles']['van']
    assert 'capacity' not in saved and saved['max_time_hours']==7.5


def records(cfg, count=5, same_customer=False):
    return InputHandler(cfg).load_data_from_json_records([
        dict(IdCust='same' if same_customer else str(i),CustName='Test',GPS='42.7,23.3',Volume=1,
             IdDoc=f'{i:010}',IdPlasDoc=str(i),IdSkld='106') for i in range(count)])


def test_no_settings_leaves_base_unchanged(setup):
    cfg, settings=setup
    before=deepcopy(cfg)
    result,info=fleet.effective(cfg,settings)
    assert result==before and cfg==before
    assert not info['notes']
    assert not fleet.store_path().exists()


def test_date_beats_weekly_and_disable_restores_base(setup):
    cfg,s=setup
    s['calendar']={'enabled':True,'entries':[entry(count=3,service=fixed(120)),entry('date',1,fixed(60))]}
    result,info=fleet.effective(cfg,s)
    assert result.vehicles[0].count==1 and result.vehicles[0].service_time_minutes==1
    assert cfg.vehicles[0].count==2
    s['calendar']['entries'][1]['enabled']=False
    result,_=fleet.effective(cfg,s)
    assert result.vehicles[0].count==3 and result.vehicles[0].service_time_minutes==2
    s['calendar']['enabled']=False
    result,_=fleet.effective(cfg,s)
    assert result.vehicles[0]==cfg.vehicles[0]


def test_count_only_then_apply_without_mutating_base(setup):
    cfg,s=setup
    s['flexible']={'van':{'enabled':True,'rules':[rule(0,3,61),rule(3,None,120)]}}
    base=deepcopy(cfg)
    with patch('main.get_distance_matrix', side_effect=AssertionError('matrix forbidden')), \
         patch('main.run_optimization', side_effect=AssertionError('solver forbidden')), \
         patch('setdata_client.send_set_data_row', side_effect=AssertionError('upload forbidden')):
        result=fleet.check_load(cfg,s,loader=lambda c:records(c,5))
    depot=fleet.depot_key(cfg.locations.depot_location)
    assert result['depots'][depot]['average']==2.5
    s['snapshots'][result['signature']]=result
    adjusted,info=fleet.effective(cfg,s)
    assert adjusted.vehicles[0].service_time_minutes==61/60
    assert '2.50' in info['notes']['van']
    assert cfg==base
    cfg.vehicles[0].count=3
    adjusted,info=fleet.effective(cfg,s)
    assert adjusted.vehicles[0].service_time_minutes==cfg.vehicles[0].service_time_minutes
    assert info['snapshot'] is None


def test_rule_boundaries_no_rounding_average_and_input_override(setup):
    cfg,s=setup
    formula={'mode':'formula','base':1,'seconds':12,'units':2,'maximum':90}
    s['flexible']={'van':{'enabled':True,'rules':[rule(0,20,100),{'min':20,'max':None,'service':formula}]}}
    depot=fleet.depot_key(cfg.locations.depot_location)
    adjusted,_=fleet.effective(cfg,s,counts={depot:{'average':20}})
    c=SimpleNamespace(volume=2.1,service_time_minutes=None)
    assert customer_service_seconds(c,adjusted.vehicles[0])==13
    c.service_time_minutes=4
    assert customer_service_seconds(c,adjusted.vehicles[0])==240


def test_schedule_changes_denominator_and_has_own_dynamic_rules(setup):
    cfg,s=setup
    s['calendar']={'enabled':True,'entries':[entry(count=1,service={'mode':'dynamic','rules':[rule(0,None,90)]})]}
    s['flexible']={'van':{'enabled':True,'rules':[rule(seconds=500)]}}
    result=fleet.check_load(cfg,s,loader=lambda c:records(c))
    depot=fleet.depot_key(cfg.locations.depot_location)
    assert result['depots'][depot]['average']==5
    s['snapshots'][result['signature']]=result
    adjusted,_=fleet.effective(cfg,s)
    assert adjusted.vehicles[0].service_time_minutes==1.5


def test_visit_count_and_excluded_orders(setup):
    cfg,s=setup
    cfg.input.excluded_document_ids='0000000000'
    key=fleet.depot_key(cfg.locations.depot_location)
    result=fleet.check_load(cfg,s,loader=lambda c:records(c,5,True))
    assert result['depots'][key]['orders']==4 and result['skipped']==1
    s['check']['count_mode']='visits'
    result=fleet.check_load(cfg,s,loader=lambda c:records(c,5,True))
    assert result['depots'][key]['orders']==1


def test_unmapped_warehouse_fails_and_zero_buses_never_divide(setup):
    cfg,s=setup
    data=records(cfg)
    data.customers[0].source_id_skld='999'
    with pytest.raises(ValueError,match='999'):fleet.check_load(cfg,s,loader=lambda c:data)
    cfg.vehicles[0].enabled=False
    result=fleet.check_load(cfg,s,loader=lambda c:records(c))
    assert result['warnings'] and all(d['average'] is None for d in result['depots'].values())


def test_save_validates_overlap_and_preserves_other_sections(setup):
    cfg,s=setup
    for rows in [[rule(0,20),rule(19,30)],[rule(0,None),rule(20,30)]]:
        with pytest.raises(ValueError,match='застъпват'):fleet.validate_rules(rows)
    saved=fleet.save({'section':'flexible','revision':fleet.revision(s),'value':{'van':{'enabled':True,'rules':[rule(0,20),rule(20,None)]}}},cfg)
    original=deepcopy(saved['flexible'])
    saved=fleet.save({'section':'accent','revision':fleet.revision(saved),'value':'#693ab7'},cfg)
    assert saved['flexible']==original and saved['accent']=='#693ab7'
    with pytest.raises(ValueError,match='променени'):fleet.save({'section':'accent','revision':'old','value':'#ffffff'},cfg)


def test_calendar_conflicts_rejected_but_date_override_allowed(setup):
    cfg,s=setup
    with pytest.raises(ValueError,match='Два активни'):
        fleet.save({'section':'calendar','revision':fleet.revision(s),'value':{'enabled':True,'entries':[entry(),entry()]}},cfg)
    saved=fleet.save({'section':'calendar','revision':fleet.revision(s),'value':{'enabled':True,'entries':[entry(),entry('date')]}},cfg)
    assert len(saved['calendar']['entries'])==2


def test_publish_and_profile_json_and_locations(setup):
    cfg,s=setup
    fleet.publish_check(cfg,loader=lambda c:records(c))
    result=operations_web.payload(cfg)
    json.dumps(result)
    assert result['effective']['snapshot']
    with pytest.raises(ValueError):fleet.validate_locations({'city_traffic_duration_multiplier':0},cfg)
    with pytest.raises(ValueError):fleet.validate_locations({'depot_location':[91,23]},cfg)
    settings=fleet.read()
    saved=fleet.save({'section':'locations','revision':fleet.revision(settings),'value':{'city_traffic_duration_multiplier':1.55}},cfg)
    assert saved['snapshots']=={}
    adjusted,_=fleet.effective(cfg,saved)
    assert adjusted.locations.city_traffic_duration_multiplier==1.55
    assert cfg.locations.city_traffic_duration_multiplier!=1.55


def test_bad_store_fails_closed(setup):
    cfg,s=setup
    fleet.store_path().write_text('{broken')
    with pytest.raises(ValueError):fleet.effective(cfg)


def test_failed_refresh_invalidates_old_snapshot(setup):
    cfg,s=setup
    fleet.publish_check(cfg,loader=lambda c:records(c))
    assert fleet.effective(cfg)[1]['snapshot']
    def broken(_): raise ValueError('Input unavailable')
    with pytest.raises(ValueError,match='Input unavailable'):
        fleet.publish_check(cfg,loader=broken)
    assert not fleet.effective(cfg)[1]['snapshot']
    assert fleet.read()['last_check_error']=='Input unavailable'


def test_ambiguous_warehouse_requires_explicit_mapping(setup):
    cfg,s=setup
    cfg.set_data.set_data_vratza_id_skld=cfg.set_data.set_data_id_skld
    with pytest.raises(ValueError,match='106'):
        fleet.check_load(cfg,s,loader=lambda c:records(c))
    s['check']['depot_map']['106']=fleet.depot_key(cfg.locations.depot_location)
    assert fleet.check_load(cfg,s,loader=lambda c:records(c))['depots'][s['check']['depot_map']['106']]['orders']==5


def test_run_receives_effective_values_without_changing_base(setup, monkeypatch):
    import main
    cfg,s=setup
    s['calendar']={'enabled':True,'entries':[entry(count=1,service=fixed(61))]}
    fleet._write(s)
    class ReachedInput(Exception): pass
    seen=[]
    def prepare(_file, _data, effective):
        seen.append(effective)
        raise ReachedInput()
    monkeypatch.setattr(main,'setup_logging',lambda:None)
    monkeypatch.setattr(main,'prepare_data',prepare)
    with pytest.raises(ReachedInput):main.run_optimization(config_override=cfg)
    assert seen[0].vehicles[0].count==1
    assert seen[0].vehicles[0].service_time_minutes==61/60
    assert cfg.vehicles[0].count==2
    assert cfg.vehicles[0].service_time_minutes!=61/60
