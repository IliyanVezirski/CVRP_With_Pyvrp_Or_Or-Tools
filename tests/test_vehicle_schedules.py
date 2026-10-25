from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest
import config
import config_gui
import cvrp_api_server as api
from cvrp_solver import ORToolsSolver
from pyvrp_solver import PyVRPSolver
from vehicle_schedules import (expand_physical_vehicles, normalise_schedules,
                               validate_fleet_schedules, route_schedule_fields)
from vehicle_numbering import format_route_bus_number
from main import vehicle_config_to_worker_dict, vehicle_configs_from_worker_dicts
from setdata_client import build_set_data_rows
from test_pyvrp_multitrip import make_vehicle, make_config, make_customers, make_matrix, DEPOT


def fleet():
    return [make_vehicle(count=3, name='Рошен', physical_bus_schedules=[
        dict(bus_id='bus-a', start_id='1004501105', max_trips=3),
        dict(bus_id='bus-b', start_id='1004501108', max_trips=2),
        dict(bus_id='bus-c', start_id='1004501110', max_trips=1),
    ])]


def test_individual_identity_ranges_and_worker_roundtrip():
    original = fleet()
    before = deepcopy(original)
    restored = vehicle_configs_from_worker_dicts([vehicle_config_to_worker_dict(v) for v in original])
    buses = expand_physical_vehicles(restored)
    assert [v.count for v in buses] == [1,1,1]
    assert [v._max_trips for v in buses] == [3,2,1]
    assert [v.name for v in buses] == ['Рошен 1','Рошен 2','Рошен 3']
    assert [route_schedule_fields(buses[0], n)['schedule_id'] for n in (1,2,3)] == ['1004501105','1004501106','1004501107']
    with pytest.raises(ValueError):
        route_schedule_fields(buses[1], 3)
    original[0].name = 'Ново име'
    assert [v.config_id for v in expand_physical_vehicles(original)] == [v.config_id for v in buses]
    assert restored == before


def test_overlap_including_inactive_and_unused_slots_is_rejected():
    vehicles = fleet()
    vehicles[0].count = 1
    vehicles.append(make_vehicle(enabled=False, physical_bus_schedules=[dict(bus_id='other',start_id='01004501110',max_trips=1)]))
    with pytest.raises(ValueError, match='повече от един'):
        validate_fleet_schedules(vehicles)


@pytest.mark.parametrize('entry', [dict(start_id='xyz',max_trips=1),dict(start_id='123',max_trips=0),dict(start_id='123',max_trips=1.5),dict(start_id='123')])
def test_invalid_range(entry):
    with pytest.raises(ValueError):
        normalise_schedules([entry])


def test_numbering_keeps_unused_reservations_and_explicit_ids_when_reordered():
    buses = expand_physical_vehicles(fleet())
    explicit = SimpleNamespace(**route_schedule_fields(buses[1]), vehicle_type='internal_bus')
    automatic = SimpleNamespace(schedule_id='', reserved_schedule_ids=buses[0]._reserved_schedule_ids, vehicle_type='internal_bus')
    output = SimpleNamespace(excel_bus_number_prefix='10045011',excel_bus_number_digits=2,excel_bus_number_start=5)
    assert format_route_bus_number(output,0,explicit,[explicit,automatic]) == '1004501108'
    assert format_route_bus_number(output,1,automatic,[explicit,automatic]) == '1004501111'
    assert format_route_bus_number(output,1,explicit,[automatic,explicit]) == '1004501108'


@pytest.mark.parametrize('solver_class', [ORToolsSolver, PyVRPSolver])
def test_distinct_limits_enter_solver_model(solver_class):
    customers = make_customers(6)
    solver = solver_class(make_config(),fleet(),customers,make_matrix(customers),[DEPOT])
    if solver_class is ORToolsSolver:
        model=solver._create_data_model()
        assert [len(model['vehicle_reload_nodes'].get(i,[])) for i in range(3)] == [2,1,0]
    else:
        model=solver._create_pyvrp_model().data()
        assert [v.max_reloads for v in model.vehicle_types()] == [2,1,0]


@pytest.mark.parametrize('solver_class', [ORToolsSolver, PyVRPSolver])
def test_solver_solves_three_trips_and_will_not_exceed_one(solver_class):
    customers=make_customers(3)
    vehicle=fleet()[0]
    vehicle.count=1
    solver=solver_class(make_config(),[vehicle],customers,make_matrix(customers),[DEPOT])
    result=solver.solve()
    assert result.is_feasible
    assert len(result.routes)==3
    assert {r.schedule_id for r in result.routes}=={'1004501105','1004501106','1004501107'}
    cfg=config.MainConfig()
    cfg.vehicles=[vehicle]
    cfg.set_data.set_data_id_grafik_template='old-fixed-template'
    rows=build_set_data_rows(result,cfg)
    assert {r['IdGrafik'] for r in rows}=={r.schedule_id for r in result.routes}
    vehicle.physical_bus_schedules[0]['max_trips']=1
    limited=solver_class(make_config(),[vehicle],customers,make_matrix(customers),[DEPOT]).solve()
    assert not limited.is_feasible


def test_desktop_and_web_preserve_per_bus_data_and_legacy_schema():
    vehicle=fleet()[0]
    cfg=config.MainConfig(vehicles=[vehicle])
    payload=api._vehicle_to_web_dict(vehicle,cfg)
    restored=api._vehicle_from_payload(cfg,payload,vehicle)
    api._validate_vehicle_configs([restored])
    assert restored.physical_bus_schedules==vehicle.physical_bus_schedules
    gui=config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
    gui.cfg=cfg
    values={'vehicle.0.'+k:v for k,v in asdict(vehicle).items()}
    values['vehicle.0.vehicle_type']='internal_bus'
    values['vehicle.0.physical_bus_schedules']=json.dumps(vehicle.physical_bus_schedules)
    with patch.object(gui,'_depot_coords_from_choice',return_value=DEPOT):
        desktop=gui._collect_vehicle_configs_from_values(values)[0]
    assert desktop.physical_bus_schedules==vehicle.physical_bus_schedules
    for literal in (api._vehicle_config_literal(restored),gui._vehicle_config_literal(desktop)):
        scope=dict(VehicleConfig=config.VehicleConfig,VehicleType=config.VehicleType)
        assert eval(literal.strip().rstrip(','),scope).physical_bus_schedules==vehicle.physical_bus_schedules
    html=api._web_gui_html(cfg.api,'127.0.0.1',18765)
    assert 'Направления за всеки бус' in html
    assert 'physical_bus_schedules: row.busSchedules' in html


def test_warehouse_capacity_uses_each_bus_limit():
    from warehouse_manager import WarehouseManager
    cfg=config.MainConfig(vehicles=fleet(),cvrp=make_config())
    manager=WarehouseManager(main_config=cfg)
    assert manager._calculate_total_vehicle_capacity(100)==60


def test_rust_rejects_individual_multitrip_limits_before_runtime():
    from vrp_rust_prototype import VRPRustPrototype, VRPRustPrototypeError
    customers=make_customers(3)
    solver=VRPRustPrototype(make_config(),fleet(),customers,make_matrix(customers),[DEPOT])
    with pytest.raises(VRPRustPrototypeError, match='индивидуален максимум'):
        solver.build_request()


def test_legacy_file_save_reload_persists_ranges(tmp_path):
    from test_legacy_config_save import legacy_source
    source=tmp_path/'config.py'
    source.write_text(legacy_source(),encoding='utf-8')
    cfg=config.MainConfig(vehicles=fleet())
    with patch.object(api.config_module,'__file__',str(source)):
        api._persist_web_gui_config(cfg,include_api=False,include_cvrp=False,include_vehicles=True)
    scope={}
    exec(compile(source.read_text(encoding='utf-8'),str(source),'exec'),scope)
    saved=scope['SavedConfig']()._create_default_vehicles()[0]
    assert saved.physical_bus_schedules==fleet()[0].physical_bus_schedules


def test_new_gui_vehicle_does_not_clone_reserved_numbers():
    gui=config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
    gui.cfg=config.MainConfig(vehicles=fleet())
    assert gui._new_vehicle_template('internal_bus').physical_bus_schedules is None


def test_zero_count_retains_reserved_numbers_in_desktop_values():
    gui=config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
    gui.cfg=config.MainConfig(vehicles=fleet())
    values={'vehicle.0.'+k:v for k,v in asdict(fleet()[0]).items()}
    values.update({'vehicle.0.count':0,'vehicle.0.vehicle_type':'internal_bus'})
    with patch.object(gui,'_depot_coords_from_choice',return_value=DEPOT):
        result=gui._collect_vehicle_configs_from_values(values)
    assert len(result)==1 and result[0].count==0
    assert result[0].physical_bus_schedules==fleet()[0].physical_bus_schedules


def test_web_script_has_valid_javascript():
    import re,shutil,subprocess
    node=shutil.which('node')
    if not node:
        pytest.skip('Node not available')
    html=api._web_gui_html(config.MainConfig().api,'127.0.0.1',18765)
    scripts=re.findall(r'<script[^>]*>(.*?)</script>',html,re.S)
    subprocess.run([node,'--check'],input='\n'.join(scripts),text=True,encoding='utf-8',check=True,capture_output=True)


def test_desktop_schedule_dialog_applies_each_bus_separately():
    import tkinter as tk
    from tkinter import ttk
    root=tk.Tk()
    root.withdraw()
    errors=[]
    root.report_callback_exception=lambda *exc: errors.append(exc)
    try:
        gui=config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
        gui.root=root
        gui.cfg=config.MainConfig(vehicles=fleet())
        gui.widgets={
            'vehicle.0.count':tk.StringVar(master=root,value='3'),
            'vehicle.0.name':tk.StringVar(master=root,value='Рошен'),
            'vehicle.0.vehicle_type':tk.StringVar(master=root,value='internal_bus'),
        }
        frame=ttk.Frame(root)
        gui._add_physical_bus_schedules(frame,0,'vehicle.0',fleet()[0])
        next(w for w in frame.winfo_children() if isinstance(w,ttk.Button)).invoke()
        assert not errors
        dialog=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
        body=dialog.winfo_children()[0]
        inputs=[w for w in body.winfo_children() if isinstance(w,ttk.Entry)]
        assert [w.get() for w in inputs]==['1004501105','3','1004501108','2','1004501110','1']
        inputs[1].delete(0,'end');inputs[1].insert(0,'2')
        with patch.object(gui,'_depot_coords_from_choice',return_value=DEPOT):
            next(w for w in body.winfo_children() if isinstance(w,ttk.Button)).invoke()
        assert not errors
        updated=json.loads(gui.widgets['vehicle.0.physical_bus_schedules'].get())
        assert [i['max_trips'] for i in updated]==[2,2,1]
    finally:
        root.destroy()
