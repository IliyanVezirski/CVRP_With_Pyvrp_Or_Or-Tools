"""Generate synthetic portal fixtures. No server, credentials or external I/O."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
import cvrp_api_server as api
import fleet_automation as fleet

def create():
    cfg=config.MainConfig(vehicles=[
        config.VehicleConfig(config.VehicleType.INTERNAL_BUS,385,5,config_id='route',name='Маршрут'),
        config.VehicleConfig(config.VehicleType.CENTER_BUS,320,1,config_id='center',name='Център'),
        config.VehicleConfig(config.VehicleType.VRATZA_BUS,385,2,config_id='vratsa',name='Враца',start_location=(43.221,23.534)),
    ])
    cfg.api.web_gui_run_defaults_json=''
    cfg.vehicles[2].start_location=cfg.locations.vratza_depot_location
    cfg.api.web_gui_endpoint='/hell'
    cfg.input.json_url='https://example.invalid/orders'
    cfg.set_data.set_data_url='https://example.invalid/setdata'
    cfg.output.route_maps_upload_url='https://example.invalid/maps'
    cfg.output.route_maps_upload_token=''
    cfg.set_data.enable_set_data_upload=False
    cfg.output.route_maps_upload_mode='disabled'
    settings=fleet.defaults()
    auth=SimpleNamespace(credential_store=SimpleNamespace(list_usernames=lambda:['dispatcher']))
    with patch.object(api,'get_config',return_value=cfg),patch.object(api,'_get_web_auth_service',return_value=auth),patch.object(fleet,'read',return_value=settings):
        return {'page':api._web_gui_html(cfg.api,'127.0.0.1',8095),
                'config':api._web_gui_config_payload('127.0.0.1',8095,refresh_from_disk=False),
                'login':api._web_gui_login_html(cfg.api)}

if __name__=='__main__':
    print(json.dumps(create(),ensure_ascii=False))
