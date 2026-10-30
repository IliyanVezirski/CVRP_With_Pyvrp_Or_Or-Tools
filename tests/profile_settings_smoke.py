"""Exercise real Settings widgets in an isolated profile; no solver/network."""
import os
from pathlib import Path
import sys
import json
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from profile_runtime import load_profile_config
load_profile_config()
import config_gui
original=tk.Tk
def hidden_root(*args,**kwargs):
    root=original(*args,**kwargs);root.withdraw();return root
tk.Tk=hidden_root
app=config_gui.ConfigGUI()
try:
    assert 'Settings' in app.root.title()
    before=app.cfg.cvrp.time_limit_seconds
    values=app._collect_values()
    key='cvrp.time_limit_seconds'
    values[key]=str(int(before)+1)
    app._apply_to_config_file(values)
    import cvrp_api_server as api
    base=api._reload_config_from_disk()
    stored=api._stored_web_run_settings(base.api,strict=True,active_only=True)
    if os.environ.get('BIZANT_SETTINGS_MODE')=='web':
        assert base.cvrp.time_limit_seconds!=float(values[key])
        assert stored['cvrp']['time_limit_seconds']==float(values[key])
    else:
        assert base.cvrp.time_limit_seconds==float(values[key])
    print(json.dumps({'ok':True,'mode':os.environ.get('BIZANT_SETTINGS_MODE','global'),'title':app.root.title()}))
finally:app.root.destroy()
