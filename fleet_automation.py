"""Optional fleet calendar and load-based service policies. No solver or uploads.

Base config.py is never rewritten. Applied values are derived from a dated,
fingerprinted load check, so editing inputs/rules cannot reuse stale counts.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

LOCK = threading.RLock()
CHECK_LOCK = threading.Lock()
SERVICE_KEYS = ('service_time_mode', 'service_time_minutes', 'service_base_seconds',
                'service_seconds_per_unit', 'service_quantity_units', 'service_max_seconds')
VEHICLE_KEYS = ('enabled', 'count', 'capacity', 'max_time_hours', 'start_time_minutes')


def store_path():
    import config
    return Path(config.__file__).resolve().parent / 'data' / 'fleet_automation.json'


def defaults():
    return dict(version=1, accent='#185de0', flexible={},
                calendar=dict(enabled=False, entries=[]), locations={},
                check=dict(enabled=False, time='16:55', weekdays=[0,1,2,3,4],
                           profile='global', count_mode='documents', depot_map={}),
                snapshots={}, last_check=None, last_check_error='', last_schedule_attempt='')


def read(path=None):
    path = Path(path or store_path())
    if not path.exists():
        return defaults()
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Невалидно хранилище за графика и правилата.')
    return {**defaults(), **value}


def _write(value, path=None):
    path = Path(path or store_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.fleet_', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def number(value, label, minimum=0, integer=False):
    if isinstance(value, bool):
        raise ValueError(f'{label}: въведи число.')
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{label}: въведи число.') from None
    if not math.isfinite(result) or result < minimum or (integer and result != int(result)):
        raise ValueError(f'{label}: невалидна стойност.')
    return int(result) if integer else result


def validate_service(value, dynamic=True):
    if not isinstance(value, dict):
        raise ValueError('Невалидно обслужване.')
    mode = value.get('mode', 'inherit')
    if mode == 'inherit':
        return {'mode': mode}
    if mode == 'dynamic' and dynamic:
        return {'mode': mode, 'rules': validate_rules(value.get('rules', []))}
    if mode == 'fixed':
        return {'mode':mode, 'seconds':number(value.get('seconds'), 'Фиксирани секунди')}
    if mode == 'formula':
        return {'mode':mode, 'base':number(value.get('base', 0), 'Начални секунди'),
                'seconds':number(value.get('seconds', 12), 'Добавени секунди'),
                'units':number(value.get('units', 1), 'Количество', 0.000001),
                'maximum':number(value.get('maximum', 900), 'Максимум секунди')}
    raise ValueError('Избери фиксирано обслужване или формула.')


def validate_rules(rows):
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError('Правилата трябва да са списък до 200 реда.')
    result = []
    for row in rows:
        low = number(row.get('min', 0), 'От заявки')
        high = None if row.get('max') in (None, '') else number(row['max'], 'До заявки')
        if high is not None and high <= low:
            raise ValueError('Горната граница трябва да е по-голяма от долната.')
        result.append(dict(min=low, max=high, service=validate_service(row.get('service', {}), False)))
    result.sort(key=lambda r:r['min'])
    for left, right in zip(result, result[1:]):
        if left['max'] is None or left['max'] > right['min']:
            raise ValueError('Диапазоните на правилата се застъпват. Горната граница е без включване.')
    return result


def vehicle_key(vehicle, index):
    return str(getattr(vehicle, 'config_id', '') or f'row:{index}')


def validate_locations(value, config):
    """Reuse the API's typed zone parsers, then validate numeric/geographic bounds."""
    if not isinstance(value, dict):
        raise ValueError('Невалидни локации.')
    from cvrp_api_server import _coerce_setting_value
    allowed = asdict(config.locations)
    result = {}
    for key, raw in value.items():
        if key not in allowed:
            raise ValueError(f'Непозната настройка за локации: {key}')
        result[key] = _coerce_setting_value('locations', key, raw, getattr(config.locations, key))
    candidate = deepcopy(config.locations)
    for key, item in result.items():
        setattr(candidate, key, item)
    def coords(point):
        if point is None:
            return
        if len(point) != 2 or not all(math.isfinite(float(x)) for x in point) or not -90 <= float(point[0]) <= 90 or not -180 <= float(point[1]) <= 180:
            raise ValueError('Координатите трябва да са ширина и дължина.')
    for key, item in asdict(candidate).items():
        if key in ('depot_location', 'center_location', 'vratza_depot_location', 'city_center_coords'):
            coords(item)
        elif key == 'depot_locations':
            for name, point in item.items():
                if not str(name).strip():
                    raise ValueError('Посочи име на депото.')
                coords(point)
        elif key == 'center_zone_polygon':
            for point in item: coords(point)
        elif isinstance(item, (float, int)) and not isinstance(item, bool):
            number(item, key)
    for zone in list(candidate.traffic_zones) + list(candidate.center_zones):
        coords(zone.center_coords)
        number(zone.radius_km, 'Радиус', 0.000001)
        if hasattr(zone, 'duration_multiplier'):
            number(zone.duration_multiplier, 'Трафик множител', 1)
        else:
            if zone.mode not in ('circle', 'polygon'):
                raise ValueError('Зоната трябва да е кръг или полигон.')
            if zone.mode == 'polygon' and len(zone.polygon) < 3:
                raise ValueError('Полигонът изисква поне три точки.')
            for point in zone.polygon: coords(point)
            number(zone.discount_priority_vehicle, 'Множител за приоритет', 0)
            number(zone.priority_vehicle_outside_penalty, 'Глоба извън зоната')
            for penalty in zone.vehicle_penalties.values(): number(penalty, 'Глоба в зоната')
    if candidate.center_zone_mode == 'polygon' and len(candidate.center_zone_polygon) < 3:
        raise ValueError('Основната център зона изисква поне три точки.')
    number(candidate.city_traffic_duration_multiplier, 'Множител за трафика', 1)
    return {key: asdict(candidate)[key] for key in result}


def save(payload, config, path=None):
    with LOCK:
        current = read(path)
        if payload.get('revision') != revision(current):
            raise ValueError('Настройките са променени. Отвори менюто отново.')
        value = deepcopy(current)
        section = payload.get('section')
        data = payload.get('value')
        keys = {vehicle_key(v,i) for i,v in enumerate(config.vehicles)}
        if section == 'accent':
            if not isinstance(data, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', data):
                raise ValueError('Избери валиден цвят.')
            value[section] = data.lower()
        elif section == 'flexible':
            if not isinstance(data, dict) or set(data) - keys:
                raise ValueError('Бусът е премахнат или променен. Обнови страницата.')
            value[section] = {k:dict(enabled=_boolean(v.get('enabled', False)), rules=validate_rules(v.get('rules', []))) for k,v in data.items()}
        elif section == 'calendar':
            entries = []
            for entry in data.get('entries', []):
                if len(entries) >= 500:
                    raise ValueError('Максимум 500 записа в графика.')
                kind = entry.get('kind', 'weekly')
                if kind not in ('weekly', 'date'):
                    raise ValueError('Избери седмичен график или конкретна дата.')
                when = str(entry.get('date', ''))
                if kind == 'date': date.fromisoformat(when)
                weekdays = sorted(set(number(x, 'Ден', 0, True) for x in entry.get('weekdays', [])))
                if any(x > 6 for x in weekdays) or (kind == 'weekly' and not weekdays):
                    raise ValueError('Избери дни от седмицата.')
                buses = {}
                for key, policy in entry.get('vehicles', {}).items():
                    if key not in keys:
                        raise ValueError('Графикът сочи премахнат бус. Обнови избора.')
                    row = {'service':validate_service(policy.get('service', {'mode':'inherit'}))}
                    for field in VEHICLE_KEYS:
                        if field not in policy: continue
                        row[field] = (_boolean(policy[field]) if field == 'enabled' else
                                      number(policy[field], field, 0.000001 if field == 'max_time_hours' else 0,
                                             field in ('count', 'start_time_minutes')))
                    if row.get('start_time_minutes', 0) >= 1440:
                        raise ValueError('Невалиден час на тръгване.')
                    buses[key] = row
                entries.append(dict(id=str(entry.get('id', len(entries))), name=str(entry.get('name', 'График'))[:120],
                                    enabled=_boolean(entry.get('enabled', True)), kind=kind, date=when,
                                    weekdays=weekdays, vehicles=buses))
            for i,left in enumerate(entries):
                for right in entries[i+1:]:
                    overlap = (left['date'] == right['date'] if left['kind'] == 'date' else bool(set(left['weekdays']) & set(right['weekdays'])))
                    if left['enabled'] and right['enabled'] and left['kind'] == right['kind'] and overlap and set(left['vehicles']) & set(right['vehicles']):
                        raise ValueError('Два активни графика за същия ден задават един и същ бус.')
            value[section] = dict(enabled=_boolean(data.get('enabled', False)), entries=entries)
        elif section == 'check':
            when = str(data.get('time', ''))
            if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', when):
                raise ValueError('Въведи час като 16:55.')
            if data.get('profile') not in ('global', 'web') or data.get('count_mode') not in ('documents', 'visits'):
                raise ValueError('Избери профил и начин на броене.')
            days = sorted(set(number(x, 'Ден', 0, True) for x in data.get('weekdays', [])))
            if not days or any(x > 6 for x in days): raise ValueError('Избери дни за проверката.')
            value[section] = dict(enabled=_boolean(data.get('enabled', False)), time=when, weekdays=days,
                                 profile=data['profile'], count_mode=data['count_mode'],
                                 depot_map={str(k).strip():str(v) for k,v in data.get('depot_map', {}).items()})
        elif section == 'locations':
            value[section] = validate_locations(data, config)
        else:
            raise ValueError('Непознат раздел.')
        if section != 'accent':
            value['snapshots'] = {}
        _write(value, path)
        return value


def _boolean(value):
    if not isinstance(value, bool): raise ValueError('Избери включено или изключено.')
    return value


def revision(settings):
    return sha256(json.dumps({k:v for k,v in settings.items() if k not in ('snapshots','last_check','last_check_error','last_schedule_attempt')}, sort_keys=True).encode()).hexdigest()


def target_date(config):
    raw = str(getattr(config.input, 'json_override_date', '') or '')
    if raw == 'current_week_saturday':
        today = datetime.now(ZoneInfo('Europe/Sofia')).date()
        return today + timedelta(days=5-today.weekday())
    if raw:
        return datetime.strptime(raw, '%d/%m/%Y').date()
    if config.input.input_source == 'http_json':
        from input_handler import InputHandler
        return date.fromisoformat(InputHandler._next_business_date())
    return datetime.now(ZoneInfo('Europe/Sofia')).date()


def apply_locations(config, settings):
    if not settings['locations']: return
    from cvrp_api_server import _coerce_setting_value
    for key, value in settings['locations'].items():
        setattr(config.locations, key, _coerce_setting_value('locations', key, value, getattr(config.locations, key)))


def schedule_for(settings, day):
    policies, reasons = {}, {}
    if settings['calendar']['enabled']:
        for kind in ('weekly','date'):
            for entry in settings['calendar']['entries']:
                matches = day.isoformat() == entry['date'] if kind == 'date' else day.weekday() in entry['weekdays']
                if entry['enabled'] and entry['kind'] == kind and matches:
                    for key, row in entry['vehicles'].items():
                        policies[key] = row
                        reasons[key] = entry['name']
    return policies, reasons


def fingerprint(config, settings, day):
    from input_handler import shared_customer_exclusions
    payload = dict(input=asdict(config.input), vehicles=[asdict(v) for v in config.vehicles],
                   locations=asdict(config.locations), exclusions=shared_customer_exclusions(config),
                   calendar=settings['calendar'], flexible=settings['flexible'], check=settings['check'], date=day.isoformat())
    payload['input']['json_override_date'] = day.isoformat()
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _service(vehicle, policy):
    if policy['mode'] == 'fixed':
        vehicle.service_time_mode = 'fixed'
        vehicle.service_time_minutes = policy['seconds'] / 60
    elif policy['mode'] == 'formula':
        vehicle.service_time_mode = 'formula'
        for field, source in [('service_base_seconds','base'),('service_seconds_per_unit','seconds'),
                              ('service_quantity_units','units'),('service_max_seconds','maximum')]:
            setattr(vehicle, field, policy[source])


def effective(config, settings=None, day=None, counts=None):
    settings = settings or read()
    result = deepcopy(config)
    apply_locations(result, settings)
    day = day or target_date(result)
    signature = fingerprint(result, settings, day)
    snapshot = settings['snapshots'].get(signature)
    counts = counts if counts is not None else (snapshot or {}).get('depots', {})
    policies, reasons = schedule_for(settings, day)
    notes = {}
    for index, vehicle in enumerate(result.vehicles):
        key = vehicle_key(vehicle, index)
        row = policies.get(key, {})
        for field in VEHICLE_KEYS:
            if field in row: setattr(vehicle, field, row[field])
        policy = row.get('service', {'mode':'inherit'})
        origin = reasons.get(key, '')
        if policy['mode'] == 'inherit':
            flex = settings['flexible'].get(key, {})
            policy = {'mode':'dynamic', 'rules':flex['rules']} if flex.get('enabled') else {'mode':'inherit'}
            if policy['mode'] == 'dynamic': origin = 'Гъвкаво обслужване'
        if policy['mode'] == 'dynamic':
            depot = depot_key(vehicle.start_location or result.locations.depot_location)
            average = counts.get(depot, {}).get('average')
            if average is None:
                notes[key] = (origin + ': нужна е проверка на натоварването').strip(': ')
                continue
            matched = next((r for r in policy['rules'] if r['min'] <= average and (r['max'] is None or average < r['max'])), None)
            if matched:
                _service(vehicle, matched['service'])
                notes[key] = f'{origin}: {average:.2f} заявки/бус · от {matched["min"]:g} до {matched["max"] if matched["max"] is not None else "∞"}'
            else:
                notes[key] = f'{origin}: {average:.2f} заявки/бус · няма подходящо правило; основно обслужване'
        else:
            _service(vehicle, policy)
            if origin: notes[key] = origin
    return result, dict(date=day.isoformat(), signature=signature, notes=notes, snapshot=snapshot)


def depot_key(coords):
    return ','.join(f'{float(x):.6f}' for x in coords)


def check_load(config, settings, day=None, loader=None):
    """Read-only input operation. It must never call a solver or output pipeline."""
    from input_handler import InputHandler, customer_has_missing_gps
    from config import get_named_depots
    from setdata_client import _parse_depot_id_skld_map
    day = day or target_date(config)
    adjusted, info = effective(config, settings, day, counts={})
    depots = {depot_key(c):dict(name=n, orders=0, buses=0, average=None) for n,c in get_named_depots(adjusted.locations).items()}
    for vehicle in adjusted.vehicles:
        if vehicle.enabled and vehicle.count:
            key = depot_key(vehicle.start_location or adjusted.locations.depot_location)
            depots.setdefault(key, dict(name=key,orders=0,buses=0,average=None))['buses'] += vehicle.count
    mapping = {}
    for warehouse, coords in ((adjusted.set_data.set_data_id_skld, adjusted.locations.depot_location),
                              (adjusted.set_data.set_data_vratza_id_skld, adjusted.locations.vratza_depot_location)):
        warehouse = str(warehouse or '').strip()
        if not warehouse:
            continue
        key = depot_key(coords)
        # Ambiguous legacy IDs need an explicit mapping, not a last-write guess.
        mapping[warehouse] = key if warehouse not in mapping or mapping[warehouse] == key else None
    for name, warehouse in _parse_depot_id_skld_map(getattr(adjusted.set_data,'set_data_depot_id_skld_map','')).items():
        if name in get_named_depots(adjusted.locations): mapping[warehouse] = depot_key(get_named_depots(adjusted.locations)[name])
    mapping.update(settings['check']['depot_map'])
    adjusted.input.enable_customer_document_grouping = False
    if adjusted.input.input_source == 'http_json': adjusted.input.json_override_date = day.strftime('%d/%m/%Y')
    data = loader(adjusted) if loader else InputHandler(adjusted).load_data()
    seen, unknown, skipped = set(), set(), 0
    for index, customer in enumerate(data.customers):
        if customer.manually_excluded or customer_has_missing_gps(customer):
            skipped += 1
            continue
        warehouse = str(getattr(customer,'source_id_skld','') or '').strip()
        key = mapping.get(warehouse)
        if not warehouse and len([k for k,v in depots.items() if v['buses']]) == 1:
            key = next(k for k,v in depots.items() if v['buses'])
        if key not in depots:
            unknown.add(warehouse or '(липсва склад)')
            continue
        identity = ((customer.id, customer.coordinates) if settings['check']['count_mode'] == 'visits'
                    else (customer.plas_doc or customer.document or f'row:{index}'))
        token = (key, str(identity))
        if token not in seen:
            seen.add(token)
            depots[key]['orders'] += 1
    if unknown:
        raise ValueError('Свържи следните складове с депа в настройките на проверката: '+', '.join(sorted(unknown)))
    for item in depots.values():
        if item['buses']:
            item['average'] = item['orders'] / item['buses']
    warnings = [f'{v["name"]}: {v["orders"]} заявки, но няма наличен бус.' for v in depots.values() if v['orders'] and not v['buses']]
    return dict(date=day.isoformat(), checked_at=datetime.now(ZoneInfo('Europe/Sofia')).isoformat(),
                signature=info['signature'], depots=depots, skipped=skipped, warnings=warnings,
                count_mode=settings['check']['count_mode'])


def publish_check(config, path=None, loader=None):
    if not CHECK_LOCK.acquire(blocking=False):
        raise ValueError('Вече се проверява натоварването. Изчакай приключването.')
    try:
        with LOCK: settings = read(path)
        result = check_load(config, settings, loader=loader)
        with LOCK:
            current = read(path)
            if revision(current) != revision(settings):
                raise ValueError('Правилата са променени по време на проверката. Провери отново.')
            current['snapshots'][result['signature']] = result
            current['snapshots'] = dict(list(current['snapshots'].items())[-20:])
            current['last_check'] = result
            current['last_check_error'] = ''
            _write(current, path)
        return result
    except Exception as exc:
        with LOCK:
            current = read(path)
            # A failed refresh must not leave an old result active for the same
            # input/date. Other independent profiles keep their own snapshots.
            if 'settings' in locals() and revision(current) == revision(settings):
                _, info = effective(config, current)
                current['snapshots'].pop(info['signature'], None)
            current['last_check_error'] = str(exc)
            _write(current, path)
        raise
    finally:
        CHECK_LOCK.release()
