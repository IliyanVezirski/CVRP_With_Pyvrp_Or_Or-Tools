from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import os
import pytest
import config
import profile_store as store
import profile_runtime as runtime
from profile_server import route,landing
from web_gui_auth import CredentialStore,WebGUIAuthService,AuthenticationFailed


@pytest.fixture
def profiles(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    (source/'config.py').write_text('original settings',encoding='utf-8')
    root=tmp_path/'application'
    for slug in ('hell','roshen'):
        store.create(root,slug,slug.title(),source,username='user',password='a')
    monkeypatch.setenv('BIZANT_HOME',str(root))
    monkeypatch.setenv('BIZANT_PROFILE','hell')
    monkeypatch.setenv('BIZANT_PROFILE_DIR',str(root/'profiles'/'hell'))
    return root


def test_profile_create_isolated_and_validated(profiles):
    cfg=store.read(profiles)
    assert len(cfg['profiles'])==2 and all(p['trial_mode'] for p in cfg['profiles'].values())
    for bad in ('../outside','Hell','a/b','api','',r'a\b'):
        with pytest.raises(ValueError):store.valid_slug(bad)
    with pytest.raises(ValueError):store.create(profiles,'hell','Duplicate',profiles/'profiles'/'roshen')


def test_gateway_routes_cannot_escape_profile(profiles):
    p=store.read(profiles)['profiles']
    assert route('/hell/api/config',p)==('hell','/hell/api/config')
    assert route('/api/roshen/tsp?x=1',p)==('roshen','/tsp?x=1')
    for path in ('/api/hell/shutdown','/api/missing/run','/hell/../roshen','/hell/%2e%2e/roshen','http://example.org/hell','/hell%2froshen'):
        with pytest.raises(ValueError):route(path,p)
    assert '/hell' in landing(p) and '/roshen' in landing(p)


def test_trial_guards_override_upload_requests_and_preserve_config(profiles):
    cfg=config.MainConfig();cfg.set_data.enable_set_data_upload=True;cfg.output.route_maps_upload_mode='effect_upload'
    result=runtime.guard_run(deepcopy(cfg))
    assert not result.set_data.enable_set_data_upload
    assert result.output.route_maps_upload_mode=='disabled'
    assert str(profiles/'profiles'/'hell') in result.output.excel_output_dir
    assert cfg.set_data.enable_set_data_upload
    assert result.api.web_gui_endpoint=='/hell'
    from current_tsp import _tsp_upload_map_enabled
    assert not _tsp_upload_map_enabled({'upload_map':True,'upload_mode':'effect_upload'},cfg)


def test_password_change_revokes_only_this_profile(profiles):
    a=WebGUIAuthService(CredentialStore(profiles/'profiles'/'hell'))
    b=WebGUIAuthService(CredentialStore(profiles/'profiles'/'roshen'))
    first=a.login('user','a').session;second=a.login('user','a').session;other=b.login('user','a').session
    long=' Ю:; '*5000
    with pytest.raises(AuthenticationFailed):a.change_password('user','wrong',long,long)
    assert a.authenticate_session(first.token)
    a.change_password('user','a',long,long)
    assert not a.authenticate_session(first.token) and not a.authenticate_session(second.token)
    assert b.authenticate_session(other.token)
    assert a.login('user',long).success and not a.login('user','a').success
    a.change_password('user',long,'x','x')
    assert a.login('user','x').success


def test_settings_detects_concurrent_write(monkeypatch):
    from types import SimpleNamespace
    from contextlib import nullcontext
    from profile_settings import save
    app=SimpleNamespace(_file_revision='old')
    monkeypatch.setattr(runtime,'config_hash',lambda:'new')
    monkeypatch.setattr(runtime,'CONFIG_LOCK',nullcontext())
    with pytest.raises(ValueError,match='променени'):save(app,{},lambda v:pytest.fail('must not write'))
