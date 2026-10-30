"""Local-only profile management. Credentials are not copied to new profiles."""
from pathlib import Path
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager


@contextmanager
def registry_lock(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with LOCK:
        with open(root/'profiles.lock','a+b') as stream:
            stream.seek(0,2)
            if not stream.tell(): stream.write(b'0');stream.flush()
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX)
            try: yield
            finally:
                stream.seek(0)
                if os.name=='nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(stream,fcntl.LOCK_UN)

LOCK=threading.RLock()


def valid_slug(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}',value) or value in {'api','health','profiles','login','assets'}:
        raise ValueError('Адресът трябва да започва с малка латинска буква и да съдържа само букви, цифри, - или _.')
    return value


def read(root):
    path=Path(root)/'profiles.json'
    if not path.exists(): return {'version':1,'port':8095,'max_parallel_runs':1,'worker_budget':4,'profiles':{}}
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('version')!=1: raise ValueError('Неподдържан формат на профилите.')
    for slug in data['profiles']: valid_slug(slug)
    return data


def write(root,data):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=root,prefix='.profiles_')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temp,root/'profiles.json')
    finally:
        if os.path.exists(temp): os.unlink(temp)


def create(root,slug,name,source,*,import_existing=False,username=None,password=None):
    root=Path(root).resolve();slug=valid_slug(slug);source=Path(source).resolve()
    if not str(name).strip(): raise ValueError('Въведи име на профила.')
    with registry_lock(root):
        data=read(root)
        target=root/'profiles'/slug
        if target.resolve().parent!=(root/'profiles').absolute() or (root/'profiles').is_symlink():
            raise ValueError('Свързани директории не са допустими за профили.')
        if slug in data['profiles'] or target.exists(): raise ValueError('Вече има профил с този адрес.')
        if not (source/'config.py').is_file(): raise ValueError('Липсва конфигурация за копиране.')
        target.mkdir(parents=True)
        try:
            shutil.copy2(source/'config.py',target/'config.py')
            (target/'data').mkdir()
            for relative in ('data/fleet_automation.json',):
                if (source/relative).exists():
                    settings=json.loads((source/relative).read_text(encoding='utf-8-sig'))
                    settings['calendar']['enabled']=False
                    settings['check']['enabled']=False
                    settings['snapshots']={}
                    (target/relative).write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
            if import_existing:
                if (source/'data/web_gui_auth.json').exists():
                    shutil.copy2(source/'data/web_gui_auth.json',target/'data/web_gui_auth.json')
            else:
                from web_gui_auth import CredentialStore
                CredentialStore(target,legacy_users='').upsert_user(username,password)
            data['profiles'][slug]={'name':str(name).strip(),'trial_mode':True,'enabled':True}
            write(root,data)
        except Exception:
            # Only this newly-created, validated profile directory is removed.
            shutil.rmtree(target)
            raise
        return target


def set_trial(root,slug,enabled):
    with registry_lock(root):
        data=read(root)
        data['profiles'][valid_slug(slug)]['trial_mode']=bool(enabled)
        write(root,data)
