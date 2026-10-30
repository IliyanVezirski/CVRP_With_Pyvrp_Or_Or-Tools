"""Web bridge for optional fleet operations; authenticated by the existing portal."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import threading

import fleet_automation as fleet

_STATE = {'running':False, 'error':'', 'result':None}
_STATE_LOCK = threading.Lock()


def profile_config(config, profile):
    result = deepcopy(config)
    if profile == 'web':
        from cvrp_api_server import _stored_web_run_settings, _apply_web_run_settings
        _apply_web_run_settings(result, _stored_web_run_settings(result.api, strict=True, active_only=True))
    elif profile != 'global':
        raise ValueError('Избери тестов или основен профил.')
    return result


def payload(config, profile='global'):
    settings = fleet.read()
    base = profile_config(config, profile)
    effective, info = fleet.effective(base, settings)
    from config import get_named_depots
    def vehicle_dict(vehicle):
        value = asdict(vehicle)
        value['vehicle_type'] = getattr(vehicle.vehicle_type, 'value', str(vehicle.vehicle_type))
        return value
    return dict(settings={k:v for k,v in settings.items() if k not in ('snapshots', 'last_schedule_attempt')},
                revision=fleet.revision(settings), profile=profile, effective=info,
                vehicles=[dict(key=fleet.vehicle_key(v,i), name=v.name, count=v.count,
                               base=vehicle_dict(base.vehicles[i]), values=vehicle_dict(v)) for i,v in enumerate(effective.vehicles)],
                locations=asdict(effective.locations),
                depots=[dict(key=fleet.depot_key(c),name=n) for n,c in get_named_depots(effective.locations).items()],
                check_state={**deepcopy(_STATE), 'error':_STATE['error'] or settings.get('last_check_error','')})


def start_check(config, profile='global'):
    cfg = profile_config(config, profile)
    with _STATE_LOCK:
        if _STATE['running']:
            raise ValueError('Вече се проверява натоварването.')
        _STATE.update(running=True,error='',result=None)
    def work():
        try:
            result = fleet.publish_check(cfg)
            with _STATE_LOCK: _STATE.update(result=result)
        except Exception as exc:
            logging.getLogger(__name__).exception('Проверката на натоварването не завърши')
            with _STATE_LOCK: _STATE.update(error=str(exc))
        finally:
            with _STATE_LOCK: _STATE.update(running=False)
    threading.Thread(target=work, name='fleet-load-check', daemon=True).start()
    return {'status':'checking'}


def start_scheduler(get_config):
    stop = threading.Event()
    def loop():
        while not stop.wait(15):
            try:
                from profile_runtime import metadata
                info=metadata()
                if info and info.get('trial_mode',True): continue
                settings = fleet.read()
                rule = settings['check']
                if not rule['enabled']: continue
                now = datetime.now(ZoneInfo('Europe/Sofia'))
                stamp = now.strftime('%Y-%m-%d %H:%M')
                if now.weekday() not in rule['weekdays'] or now.strftime('%H:%M') != rule['time']: continue
                with fleet.LOCK:
                    settings = fleet.read()
                    if settings['last_schedule_attempt'] == stamp: continue
                    if _STATE['running']: continue
                    settings['last_schedule_attempt'] = stamp
                    fleet._write(settings)
                start_check(get_config(), rule['profile'])
            except Exception:
                logging.getLogger(__name__).exception('Грешка в графика за проверка на натоварването')
    threading.Thread(target=loop, name='fleet-load-scheduler', daemon=True).start()
    return stop


def theme_css():
    import re
    try: accent = fleet.read()['accent']
    except Exception: accent = '#185de0'
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent): accent = '#185de0'
    rgb = [int(accent[i:i+2],16) for i in (1,3,5)]
    def mix(amount): return '#'+''.join(f'{round(x*amount):02x}' for x in rgb)
    # Pick contrasting foreground even for very light user-selected accents.
    light = sum(w*(v/255/12.92 if v/255 <= .04045 else ((v/255+.055)/1.055)**2.4) for w,v in zip((.2126,.7152,.0722),rgb))
    foreground = '#101827' if light > .179 else '#ffffff'
    return (f':root{{--accent:{accent};--accent-ink:{foreground};--accent-dark:{mix(.42)};--accent-hover:{mix(.82)};}}'
            'header{background:var(--accent-dark)!important}button.primary,.primary{background:var(--accent)!important;color:var(--accent-ink)!important;border-color:var(--accent)!important}'
            'button.primary:hover{background:var(--accent-hover)!important}input[type=checkbox]{accent-color:var(--accent)}'
            'button:focus-visible,input:focus-visible,select:focus-visible{outline-color:var(--accent)!important}'
            '.portal-nav button[aria-selected=true]{background:color-mix(in srgb,var(--accent) 12%,white);color:var(--accent-dark)}'
            '.portal-nav button[aria-selected=true]{border-bottom-color:var(--accent)!important}.vehicle-extra-toggle[aria-expanded=true]{color:var(--accent-dark)!important}'
            '#executionProgress .progress-bar-fill,#executionProgress .progress-fill{background:var(--accent)!important}'
            '.vehicle-row{border-left-color:var(--accent)!important}.run-progress-fill{background:var(--accent)!important}')
