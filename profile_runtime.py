"""Process-local profile identity. Never switch a live solver's global config."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys
import threading


def home():
    return Path(os.environ.get('BIZANT_HOME') or (Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent)).resolve()


def profile_dir():
    value=os.environ.get('BIZANT_PROFILE_DIR')
    return Path(value).resolve() if value else None


def data_dir():
    return profile_dir() or home()


def metadata():
    if not profile_dir(): return None
    registry=json.loads((home()/'profiles.json').read_text(encoding='utf-8'))
    return registry['profiles'][os.environ['BIZANT_PROFILE']]


def apply_identity(cfg):
    if not profile_dir(): return cfg
    slug=os.environ['BIZANT_PROFILE']
    cfg.api.web_gui_endpoint='/'+slug
    cfg.api.web_gui_enabled=True
    cfg.api.web_gui_title=metadata()['name']
    cfg.api.api_host='127.0.0.1'
    registry=json.loads((home()/'profiles.json').read_text(encoding='utf-8'))
    cfg.api.api_port=int(os.environ.get('BIZANT_INTERNAL_PORT') or registry['port'])
    origin=os.environ.get('BIZANT_PUBLIC_ORIGIN','http://127.0.0.1:'+str(os.environ.get('BIZANT_PUBLIC_PORT','8095'))).rstrip('/')
    cfg.api.api_public_url=origin+'/api/'+slug
    cfg.api.web_gui_public_url=origin+'/'+slug
    cfg.api.web_gui_trusted_proxy_ips='127.0.0.1'
    cfg.logging.log_file=str(profile_dir()/'logs'/'cvrp.log')
    cfg.cache.cache_dir=str(profile_dir()/'cache')
    cfg.api.tsp_daily_report_history_file=str(profile_dir()/'logs'/'tsp_routes_history.jsonl')
    cfg.api.tsp_daily_report_output_dir=str(profile_dir()/'output'/'tsp_reports')
    if metadata().get('trial_mode',True):
        if hasattr(cfg.api,'tsp_daily_report_enabled'): cfg.api.tsp_daily_report_enabled=False
    return cfg


def guard_run(cfg):
    """Isolated trial outputs and no external writes, even with API overrides."""
    info=metadata()
    if not info: return cfg
    apply_identity(cfg)
    budget=int(os.environ.get('BIZANT_WORKER_BUDGET','4'))
    cfg.cvrp.num_workers=min(cfg.cvrp.num_workers,budget) if cfg.cvrp.num_workers>0 else budget
    if not info.get('trial_mode',True): return cfg
    for key in vars(cfg.set_data):
        if key.startswith('enable_'): setattr(cfg.set_data,key,False)
    cfg.output.route_maps_upload_mode='disabled'
    for key in ('enable_ftp_upload','enable_sftp_upload','enable_upload'):
        if hasattr(cfg.output,key): setattr(cfg.output,key,False)
    root=profile_dir()/'output'
    for key,relative in {'excel_output_dir':'excel','routes_output_dir':'routes','charts_output_dir':'charts',
                         'map_output_file':'interactive_map.html','csv_output_file':'routes.csv'}.items():
        setattr(cfg.output,key,str(root/relative))
    return cfg


def load_profile_config():
    directory=profile_dir()
    if not directory: return
    from config_compat import compile_external_config
    path=directory/'config.py'
    spec=importlib.util.spec_from_file_location('config',path)
    module=importlib.util.module_from_spec(spec)
    sys.modules['config']=module
    exec(compile_external_config(path.read_text(encoding='utf-8-sig'),str(path)),module.__dict__)
    apply_identity(module.get_config())
    os.chdir(directory)


def config_hash():
    import config
    return hashlib.sha256(Path(config.__file__).read_bytes()).hexdigest()


class ConfigLock:
    """Re-entrant thread + cross-process lock shared by Web and Settings."""
    def __init__(self):
        self.lock=threading.RLock()
        self.local=threading.local()

    def __enter__(self):
        self.lock.acquire()
        try:
            depth=getattr(self.local,'depth',0)
            if not depth:
                import config
                path=Path(config.__file__).parent/'data'/'config.lock'
                path.parent.mkdir(parents=True,exist_ok=True)
                stream=open(path,'a+b')
                stream.seek(0,2)
                if not stream.tell(): stream.write(b'0');stream.flush()
                stream.seek(0)
                try:
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream,fcntl.LOCK_EX)
                except Exception: stream.close();raise
                self.local.stream=stream
            self.local.depth=depth+1
            return self
        except Exception: self.lock.release();raise

    def __exit__(self,*args):
        self.local.depth-=1
        if not self.local.depth:
            stream=self.local.stream
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_UN)
            stream.close()
        self.lock.release()


CONFIG_LOCK=ConfigLock()
