"""Persistent per-physical-vehicle direction ranges and solver trip limits."""
from copy import deepcopy
import re
from uuid import uuid4
import json


def normalise_schedules(value):
    if isinstance(value, str):
        value = json.loads(value)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('Направленията трябва да са списък от отделни бусове.')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Невалидни настройки за отделен бус.')
        start = str(item.get('start_id', '') or '').strip()
        raw_limit = item.get('max_trips', None)
        limit = None
        if raw_limit not in (None, ''):
            if isinstance(raw_limit, bool) or not re.fullmatch(r'[0-9]+', str(raw_limit)):
                raise ValueError('Максимум курсове трябва да е цяло число.')
            limit = int(raw_limit)
            if not 1 <= limit <= 1000:
                raise ValueError('Максимум курсове трябва да е между 1 и 1000.')
        if start and (not re.fullmatch(r'[0-9]+', start) or int(start) <= 0):
            raise ValueError('Началното ID трябва да съдържа само цифри и да е положително.')
        if start and limit is None:
            raise ValueError('Задай максимум курсове за запазеното начално ID.')
        result.append(dict(bus_id=str(item.get('bus_id') or uuid4().hex), start_id=start, max_trips=limit))
    return result


def schedule_ids(item):
    start = item.get('start_id', '')
    return [str(int(start) + n).zfill(len(start)) for n in range(item['max_trips'])] if start else []


def validate_fleet_schedules(vehicles):
    identities, numbers = set(), set()
    for vehicle in vehicles:
        for item in normalise_schedules(getattr(vehicle, 'physical_bus_schedules', None)):
            if item['bus_id'] in identities:
                raise ValueError('Един и същ отделен бус е зададен повече от веднъж.')
            identities.add(item['bus_id'])
            for number in schedule_ids(item):
                # Numeric comparison also catches differently padded aliases.
                if int(number) in numbers:
                    raise ValueError(f'Направление {number} е запазено за повече от един бус.')
                numbers.add(int(number))


def expand_physical_vehicles(vehicles):
    """Split configured groups only, without mutating persisted configurations."""
    validate_fleet_schedules(vehicles)
    if not any(getattr(v, 'physical_bus_schedules', None) for v in vehicles):
        return list(vehicles)
    reserved = tuple(number for v in vehicles
                     for item in normalise_schedules(getattr(v, 'physical_bus_schedules', None))
                     for number in schedule_ids(item))
    result = []
    for vehicle in vehicles:
        items = normalise_schedules(getattr(vehicle, 'physical_bus_schedules', None))
        if not items:
            v = deepcopy(vehicle)
            v._reserved_schedule_ids = reserved or getattr(vehicle, '_reserved_schedule_ids', ())
            result.append(v)
            continue
        for index in range(int(vehicle.count)):
            v = deepcopy(vehicle)
            item = items[index] if index < len(items) else None
            v.count = 1
            v.name = f'{vehicle.name} {index + 1}' if vehicle.count > 1 else vehicle.name
            v.config_id = item['bus_id'] if item else f'{vehicle.config_id}:physical:{index + 1}'
            v.physical_bus_schedules = None
            v._schedule_start_id = item['start_id'] if item else ''
            v._max_trips = item['max_trips'] if item else None
            v._reserved_schedule_ids = reserved
            result.append(v)
    return result


def route_schedule_fields(vehicle, trip_number=1):
    limit = getattr(vehicle, '_max_trips', None)
    if limit is not None and trip_number > limit:
        raise ValueError(f'Бусът надвишава разрешените {limit} курса.')
    start = getattr(vehicle, '_schedule_start_id', '')
    return dict(schedule_id=str(int(start) + trip_number - 1).zfill(len(start)) if start else '',
                reserved_schedule_ids=getattr(vehicle, '_reserved_schedule_ids', ()))
