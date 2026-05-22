"""
Централен конфигурационен файл за CVRP програма с OSRM
Съдържа всички настройки за всички модули
"""

import os
import math
from dataclasses import dataclass, field
# from tkinter import TRUE  # Премахнато за EXE съвместимост
TRUE = True  # Заменяме tkinter.TRUE с Python True
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum


# --- Path Configuration ---
# Определяне на основната директория на проекта.
# __file__ е пътят до текущия файл (config.py).
# os.path.dirname(__file__) е директорията, в която се намира config.py.
# Това прави всички пътища независими от директорията, от която се стартира скриптът.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Helper функция за създаване на абсолютни пътища
def _abs_path(relative_path: str) -> str:
    return os.path.join(PROJECT_ROOT, relative_path)


def build_ordered_depots(
    main_depot: Tuple[float, float],
    vehicle_configs: Optional[List[Any]] = None,
) -> List[Tuple[float, float]]:
    """Връща единния ред на депата за матрици и solver-и."""
    def _normalise_coords(coords: Any) -> Optional[Tuple[float, float]]:
        try:
            if not coords or len(coords) != 2:
                return None
            return (float(coords[0]), float(coords[1]))
        except (TypeError, ValueError):
            return None

    def _coord_key(coords: Tuple[float, float]) -> Tuple[float, float]:
        return (round(coords[0], 6), round(coords[1], 6))

    main_depot = _normalise_coords(main_depot)
    if main_depot is None:
        return []

    depots = [main_depot]
    seen = {_coord_key(main_depot)}
    extra_depots = []

    for vehicle_config in vehicle_configs or []:
        if not getattr(vehicle_config, "enabled", False):
            continue
        for attr_name in ("start_location", "end_location"):
            location = _normalise_coords(getattr(vehicle_config, attr_name, None))
            if location and _coord_key(location) not in seen:
                extra_depots.append(location)
                seen.add(_coord_key(location))

    depots.extend(sorted(extra_depots, key=lambda coords: (coords[0], coords[1])))
    return depots


class RoutingEngine(Enum):
    """Избор на routing engine за изчисляване на матрици."""
    OSRM = "osrm"          # Open Source Routing Machine - бърз, но без traffic
    VALHALLA = "valhalla"  # Valhalla - поддържа time-dependent routing и traffic


class VehicleType(Enum):
    """Типове превозни средства, използвани в системата."""
    INTERNAL_BUS = "internal_bus"  # Стандартен бус за вътрешни маршрути
    CENTER_BUS = "center_bus"      # Специализиран бус за централна градска част
    EXTERNAL_BUS = "external_bus"  # Бус за дълги, извънградски маршрути
    SPECIAL_BUS = "special_bus"    # Нов тип бус със специален режим на работа
    VRATZA_BUS = "vratza_bus"      # Бус от депо Враца
    WAREHOUSE = "warehouse"        # Виртуален тип за заявки, които се обработват от склад
    DISABLED = "disabled"          # Тип за изключени от употреба превозни средства


@dataclass
class VehicleConfig:
    """Конфигурация за един тип превозно средство."""
    vehicle_type: VehicleType  # Тип на превозното средство (от VehicleType enum)
    capacity: int              # Максимален капацитет/обем (в стекове, грамове или друга единица)
    count: int                 # Брой налични превозни средства от този тип
    name: str = ""             # Име на буса за отчети и карти. При count > 1 се показва като "Име 1", "Име 2"...
    fixed_cost: int = 40000    # Цена/глоба за използване на един бус. По-висока стойност намалява броя използвани бусове.
    max_distance_km: Optional[int] = None  # Максимален пробег в километри за един маршрут. None означава без лимит.
    max_time_hours: int = 8    # Максимално време за работа по един маршрут в часове (включва пътуване и обслужване).
    service_time_minutes: int = 15 # Средно време за обслужване на един клиент в минути. Добавя се към общото време на маршрута.
    enabled: bool = True       # Дали този тип превозно средство е активно и може да се използва от solver-а.
    start_location: Optional[Tuple[float, float]] = None  # Персонална начална точка (депо) за този тип. Ако е None, използва се главното депо.
    end_location: Optional[Tuple[float, float]] = None  # Персонална крайна точка за този тип. Ако е None, маршрутът завършва в стартовото депо.
    max_customers_per_route: Optional[int] = None # Максимален брой клиенти, които могат да бъдат обслужени в един маршрут. None = без ограничение.
    start_time_minutes: int = 480  # Стартово време в минути от 00:00 (8:00 = 480 минути)
    tsp_depot_location: Optional[Tuple[float, float]] = None  # Депо за TSP оптимизация. Ако е None, използва start_location или главното депо.


@dataclass
class TrafficZoneConfig:
    """Допълнителна зона, в която времето за движение се умножава."""
    name: str
    center_coords: Tuple[float, float]
    radius_km: float
    duration_multiplier: float
    enabled: bool = True


@dataclass
class LocationConfig:
    """GPS координати за важни локации в системата."""
    depot_location: Tuple[float, float] = (42.695785029219415, 23.23165887245312)  # Главно депо, от което тръгват повечето превозни средства.
    center_location: Tuple[float, float] = (42.69735652560932, 23.323809998750914) # Специална локация "Център", използвана за CENTER_BUS.
    vratza_depot_location: Tuple[float, float] = (43.221042895146915, 23.5344026186417)  # Депо във Враца
    depot_locations: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {})
    center_zone_mode: str = "polygon"  # "circle" = радиус, "polygon" = начертана зона
    center_zone_polygon: List[Tuple[float, float]] = field(default_factory=lambda: [
        (42.70770023, 23.29886913),
        (42.70672263, 23.30091834),
        (42.70618653, 23.30545664),
        (42.70652554, 23.30671191),
        (42.70546908, 23.32292318),
        (42.70684089, 23.3228159),
        (42.70734546, 23.32413554),
        (42.70821268, 23.32922101),
        (42.70804712, 23.33158135),
        (42.7071799, 23.33274007),
        (42.70574502, 23.3329761),
        (42.70532716, 23.33366275),
        (42.70472797, 23.33340526),
        (42.69980805, 23.34409118),
        (42.69772643, 23.34685922),
        (42.69643326, 23.34652662),
        (42.69754507, 23.35490584),
        (42.69555011, 23.35489511),
        (42.69249841, 23.35491657),
        (42.68736458, 23.35199833),
        (42.68569265, 23.35019588),
        (42.68305058, 23.34660172),
        (42.68971468, 23.33787918),
        (42.68578729, 23.33202124),
        (42.68569265, 23.33164573),
        (42.68658383, 23.33047628),
        (42.68223034, 23.31735492),
        (42.68174922, 23.31405044),
        (42.68189119, 23.3107996),
        (42.68401278, 23.3136642),
        (42.68675733, 23.31032753),
        (42.68421784, 23.30075741),
        (42.68656017, 23.29970598),
        (42.68691506, 23.29928756),
        (42.69073986, 23.30701232),
        (42.69270343, 23.30527425),
        (42.69443038, 23.30867529),
        (42.69502967, 23.3084929),
        (42.69842031, 23.30952287),
        (42.70035998, 23.2969594)
    ])  # Точки на полигона: [(lat, lon), ...]
    center_zone_radius_km: float = 1.9  # Радиус на център зоната в километри
    enable_center_zone_priority: bool = True  # Дали да се прилага приоритет за център зоната
    
    # Параметри за глобата на останалите бусове за влизане в центъра
    external_bus_center_penalty: float = 40000.0  # Множител за глоба на EXTERNAL_BUS за влизане в центъра
    internal_bus_center_penalty: float = 40000.0   # Множител за глоба на INTERNAL_BUS за влизане в центъра
    special_bus_center_penalty: float = 40000.0    # Множител за глоба на SPECIAL_BUS за влизане в центъра
    vratza_bus_center_penalty: float = 40000.0   # Множител за глоба на VRATZA_BUS за влизане в центъра (като EXTERNAL_BUS)
    enable_center_zone_restrictions: bool = True  # Дали да се прилагат ограничения за влизане в центъра
    discount_center_bus: float = 0.9  # Отстъпка за CENTER_BUS в център зоната (намалява разходите с 90%)
    center_bus_outside_center_penalty: float = 0.0  # Глоба за CENTER_BUS при обслужване извън център зоната
    
    # Параметри за градски трафик (задръствания в София)
    city_center_coords: Tuple[float, float] = (42.6977, 23.3219)  # Център на София (площад Независимост)
    city_traffic_radius_km: float = 10.0  # Радиус на градската зона с трафик (км)
    city_traffic_duration_multiplier: float = 1.55 # Множител за време в града (1.35 = +35% заради трафик)
    enable_city_traffic_adjustment: bool = True  # Дали да се прилага корекция за градски трафик
    traffic_zones: List[TrafficZoneConfig] = field(default_factory=lambda: [])


def _distance_km(coord1: Optional[Tuple[float, float]], coord2: Tuple[float, float]) -> float:
    if not coord1 or not coord2:
        return 0.0

    lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def is_point_in_polygon(point: Optional[Tuple[float, float]], polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon check for (lat, lon) coordinates."""
    if not point or len(polygon) < 3:
        return False

    lat, lon = point
    inside = False
    j = len(polygon) - 1

    for i in range(len(polygon)):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]
        intersects = ((lon_i > lon) != (lon_j > lon)) and (
            lat < (lat_j - lat_i) * (lon - lon_i) / ((lon_j - lon_i) or 1e-12) + lat_i
        )
        if intersects:
            inside = not inside
        j = i

    return inside


def is_location_in_center_zone(coords: Optional[Tuple[float, float]], location_config: LocationConfig) -> bool:
    if not coords:
        return False

    mode = getattr(location_config, "center_zone_mode", "circle")
    polygon = getattr(location_config, "center_zone_polygon", [])
    if str(mode).lower() == "polygon" and len(polygon) >= 3:
        return is_point_in_polygon(coords, polygon)

    return _distance_km(coords, location_config.center_location) <= location_config.center_zone_radius_km


def get_traffic_zones(location_config: Optional[LocationConfig]) -> List[TrafficZoneConfig]:
    """Връща всички активни трафик зони, включително старите single-zone настройки."""
    if not location_config or not getattr(location_config, "enable_city_traffic_adjustment", False):
        return []

    zones = []
    legacy_center = getattr(location_config, "city_center_coords", None)
    legacy_radius = getattr(location_config, "city_traffic_radius_km", 0) or 0
    legacy_multiplier = getattr(location_config, "city_traffic_duration_multiplier", 1.0) or 1.0
    if legacy_center and legacy_radius > 0 and legacy_multiplier > 1.0:
        zones.append(
            TrafficZoneConfig(
                name="Основна зона",
                center_coords=legacy_center,
                radius_km=float(legacy_radius),
                duration_multiplier=float(legacy_multiplier),
                enabled=True,
            )
        )

    for zone in getattr(location_config, "traffic_zones", []) or []:
        if isinstance(zone, dict):
            zone = TrafficZoneConfig(
                name=str(zone.get("name", "Traffic zone")),
                center_coords=tuple(zone.get("center_coords", legacy_center or (0.0, 0.0))),
                radius_km=float(zone.get("radius_km", 0) or 0),
                duration_multiplier=float(zone.get("duration_multiplier", 1.0) or 1.0),
                enabled=bool(zone.get("enabled", True)),
            )
        if (
            getattr(zone, "enabled", True)
            and getattr(zone, "center_coords", None)
            and getattr(zone, "radius_km", 0) > 0
            and getattr(zone, "duration_multiplier", 1.0) > 1.0
        ):
            zones.append(zone)

    return zones


def get_traffic_multiplier(
    location_config: Optional[LocationConfig],
    from_coords: Optional[Tuple[float, float]],
    to_coords: Optional[Tuple[float, float]],
) -> float:
    """Връща най-силния multiplier, ако и двете точки попадат в една трафик зона."""
    if not from_coords or not to_coords:
        return 1.0

    multiplier = 1.0
    for zone in get_traffic_zones(location_config):
        center = getattr(zone, "center_coords", None)
        radius = float(getattr(zone, "radius_km", 0) or 0)
        if not center or radius <= 0:
            continue
        if _distance_km(from_coords, center) <= radius and _distance_km(to_coords, center) <= radius:
            multiplier = max(multiplier, float(getattr(zone, "duration_multiplier", 1.0) or 1.0))

    return multiplier


def describe_center_zone(location_config: LocationConfig) -> str:
    mode = getattr(location_config, "center_zone_mode", "circle")
    polygon = getattr(location_config, "center_zone_polygon", [])
    if str(mode).lower() == "polygon" and len(polygon) >= 3:
        return f"полигон с {len(polygon)} точки"
    return f"радиус {location_config.center_zone_radius_km} км"


def get_named_depots(location_config: LocationConfig) -> Dict[str, Tuple[float, float]]:
    depots = {
        "Главно депо": location_config.depot_location,
        "Център": location_config.center_location,
        "Враца": location_config.vratza_depot_location,
    }
    depots.update(getattr(location_config, "depot_locations", {}) or {})
    return depots

def _nearest_depot_distance_km(
    coords: Optional[Tuple[float, float]],
    depot_locations: Any,
) -> float:
    if not coords or not depot_locations:
        return float("inf")

    if (
        isinstance(depot_locations, tuple)
        and len(depot_locations) == 2
        and all(isinstance(value, (int, float)) for value in depot_locations)
    ):
        depot_iterable = [depot_locations]
    else:
        depot_iterable = depot_locations

    distances = [
        _distance_km(coords, depot)
        for depot in depot_iterable
        if depot and len(depot) == 2
    ]
    return min(distances) if distances else float("inf")


def calculate_customer_drop_penalties(
    customers: List[Any],
    depot_locations: Any,
    solver_config: Any,
) -> List[int]:
    """Return per-customer skip penalties/prizes.

    Higher volume and closer-to-depot customers get lower values, so they are
    the first candidates for skipping when constraints cannot all be satisfied.
    """
    base_penalty = int(getattr(solver_config, "distance_penalty_disjunction", 45000))
    if not getattr(solver_config, "enable_priority_dropping", False):
        return [base_penalty for _ in customers]

    min_penalty = max(1, int(getattr(solver_config, "min_customer_drop_penalty", 20000)))
    max_penalty = max(min_penalty, int(getattr(solver_config, "max_customer_drop_penalty", 120000)))
    volume_weight = max(0.0, float(getattr(solver_config, "drop_volume_weight", 0.70)))
    closeness_weight = max(0.0, float(getattr(solver_config, "drop_closeness_weight", 0.30)))
    total_weight = volume_weight + closeness_weight or 1.0

    volumes = [max(0.0, float(getattr(customer, "volume", 0.0) or 0.0)) for customer in customers]
    distances = [
        _nearest_depot_distance_km(getattr(customer, "coordinates", None), depot_locations)
        for customer in customers
    ]

    max_volume = max(volumes) if volumes else 0.0
    finite_distances = [distance for distance in distances if math.isfinite(distance)]
    max_distance = max(finite_distances) if finite_distances else 0.0

    penalties: List[int] = []
    penalty_range = max_penalty - min_penalty

    for volume, distance in zip(volumes, distances):
        volume_score = volume / max_volume if max_volume > 0 else 0.0
        closeness_score = 0.0
        if max_distance > 0 and math.isfinite(distance):
            closeness_score = 1.0 - min(distance / max_distance, 1.0)

        drop_score = (
            volume_score * volume_weight + closeness_score * closeness_weight
        ) / total_weight
        penalty = max_penalty - int(round(drop_score * penalty_range))
        penalties.append(max(min_penalty, min(max_penalty, penalty)))

    return penalties


@dataclass
class RoutingConfig:
    """Конфигурация за избор на routing engine."""
    engine: RoutingEngine = RoutingEngine.OSRM # Кой routing engine да се използва: OSRM или VALHALLA
    # Ако е VALHALLA и enable_time_dependent е True, ще се използва time-dependent routing
    enable_time_dependent: bool = False  # Дали да се използва time-dependent routing (само за Valhalla)
    departure_time: str = "08:00"  # Час на тръгване (HH:MM) за time-dependent routing
    enable_curbside_approach: bool = False  # Ако е True, маршрутите се строят така, че клиентът да е от правилната страна на улицата.
    valhalla_preferred_side: str = "same"  # same = клиентът да е от страната на движение; either = без ограничение.


@dataclass
class ValhallaConfig:
    """Конфигурации за връзка с Valhalla сървър."""
    base_url: str = "http://localhost:8002"  # Адрес на Valhalla сървъра
    costing: str = "auto"  # Профил: "auto", "truck", "bicycle", "pedestrian"
    timeout_seconds: int = 60  # Максимално време за изчакване
    retry_attempts: int = 3  # Брой опити при неуспешна заявка
    retry_delay_seconds: int = 1  # Време за изчакване между опитите
    use_cache: bool = False  # Дали да се кешират резултатите
    cache_expiry_hours: int = 24  # Време за валидност на кеша
    
    # Time-dependent routing настройки
    date_time_type: int = 1  # 0=current, 1=depart_at, 2=arrive_by
    
    # Truck-specific настройки (ако costing="auto")
    truck_height: float = 3.5  # Височина в метри
    truck_width: float = 2.5   # Ширина в метри
    truck_weight: float = 10.0  # Тегло в тонове


@dataclass
class OSRMConfig:
    """Конфигурации за връзка с OSRM (Open Source Routing Machine) сървър за изчисляване на разстояния и времена."""
    base_url: str = "http://localhost:5000"  # Адрес на локално инсталиран OSRM сървър.
    profile: str = "driving"  # Профил за маршрутизация. Възможни: "driving", "walking", "cycling".
    chunk_size: int = 80  # Брой локации, които се изпращат в една заявка към OSRM. По-голям chunk = по-малко заявки, но повече памет.
    timeout_seconds: int = 45  # Максимално време за изчакване на отговор от OSRM сървъра.
    retry_attempts: int = 3    # Брой опити при неуспешна OSRM заявка.
    retry_delay_seconds: int = 1 # Време за изчакване между неуспешните опити.
    average_speed_kmh: float = 40.0 # Резервна средна скорост, ако OSRM не върне време за пътуване.
    use_cache: bool = False # Дали да се кешират резултатите от OSRM, за да се избегнат повторни заявки.
    cache_expiry_hours: int = 24 # Време, след което кешът се счита за невалиден.
    
    # Настройки за резервен (fallback) OSRM сървър
    fallback_to_public: bool = True  # Ако локалният сървър не отговаря, да се използва ли публичният.
    public_osrm_url: str = "http://router.project-osrm.org"  # Адрес на публичния OSRM сървър.
    
    # Настройки за оптимизация на OSRM заявките
    max_locations_for_osrm: int = 50 # Максимален брой локации, за които се прави реална заявка. Над този брой се използват приблизителни изчисления.
    enable_smart_chunking: bool = True # Интелигентно разделяне на заявките на части за по-голяма ефективност.


@dataclass
class InputConfig:
    """Конфигурации за обработка на входните данни от Excel файл или HTTP JSON."""
    input_source: str = "excel"  # Източник на данни: "excel" или "http_json"
    excel_file_path: str = _abs_path("C:\\Users\\shaman\\Documents\\New project 2\\CVRP_With_Pyvrp_Or_Or-Tools\\data/input.xlsx") # Път до входния Excel файл.
    json_url: str = "http://sio.effect.bg:7080/lubiv_Bizant"  # URL за HTTP JSON източник (използва се когато input_source="excel")
    json_http_method: str = "GET"  # HTTP метод за JSON източника: "GET" или "POST".
    json_command: str = "getData"  # Стойност за cmd параметъра при HTTP JSON заявка.
    json_sklad: str = "106"  # Стойност за Sklad параметъра.
    json_done_flag: str = "1974"  # Стойност за DoneFlag параметъра.
    json_extra_query: str = ""  # Допълнителни GET параметри във формат key=value&key2=value2.
    json_date_field: str = "Date"  # Име на полето/параметъра за датата при HTTP JSON заявка.
    json_gps_field: str = "GPS"          # Име на JSON полето с GPS координати.
    json_client_id_field: str = "IdCust"  # Име на JSON полето с клиентски номер.
    json_client_name_field: str = "CustName"  # Име на JSON полето с име на клиента.
    json_volume_field: str = "Volume"     # Име на JSON полето с брой стекове.
    json_document_field: str = "IdDoc"  # Име на JSON полето с номер на документа.
    json_plas_doc_field: str = "IdPlasDoc"  # Име на JSON полето за IdPlasDoc, което се връща към setData.
    json_id_skld_field: str = "IdSkld"  # Име на JSON полето с оригиналния склад на заявката.
    json_time_window_field: str = "WorkTime"  # Име на JSON полето с работно време във формат "08:00 - 16:00".
    json_delivery_comment_field: str = "DeliveryComment"  # Име на JSON полето с коментар/инструкция за доставката.
    json_override_date: str = "21/05/2026"  # Конкретна дата (DD/MM/YYYY). Ако е празно, автоматично се изчислява следващият работен ден.
    json_timeout_seconds: int = 30  # Таймаут за HTTP заявката в секунди.
    gps_column: str = "GPS"         # Име на колоната с GPS координатите на клиентите.
    client_id_column: str = "IdCust"      # Име на колоната с ID на клиента.
    client_name_column: str = "Клиент" # Име на колоната с името на клиента.
    volume_column: str = "Брой стекове"           # Име на колоната с обема/теглото на заявката.
    document_column: str = "Документ"  # Име на колоната с номер на документа/поръчката.
    time_window_column: str = "Работно време"  # Excel колона с работно време във формат "08:00 - 16:00".
    delivery_comment_column: str = "Коментар доставка"  # Excel колона с коментар/инструкция за доставката.
    enable_customer_document_grouping: bool = True  # Групира няколко документа за един и същ клиент/GPS в едно посещение.
    sheet_name: Optional[str] = None  # Име на листа в Excel файла. Ако е None, използва се първият наличен.
    encoding: str = "utf-8"           # Кодировка на файла.


@dataclass
class WarehouseConfig:
    """Конфигурации за логиката на склада, който обработва част от заявките предварително."""
    enable_warehouse: bool = False      # Дали да се използва логиката за предварително отделяне на заявки за склада
    sort_by_volume: bool = True        # Дали заявките да се сортират по обем (от най-малък към най-голям) преди обработка
    sort_by_distance: bool = True      # Дали да се сортират по разстояние за клиенти с еднакъв обем (от най-далечен към най-близък)
    check_max_bus_capacity: bool = True # Проверява дали клиент надвишава капацитета на най-големия наличен бус
    max_bus_customer_volume: float = 20000.0 # Максимален обем на клиент (стекове), над който се изпращат към склада, а не към бусовете
    capacity_toleranse: float = 1.0 # Толеранс на капацитета на превозните средства.
@dataclass
class CVRPConfig:
    """
    Конфигурация за CVRP (Capacitated Vehicle Routing Problem) решателя.
    Тези настройки контролират всеки аспект на процеса на оптимизация.
    Поддържа OR-Tools и PyVRP солвъри.
    """
    solver_type: str = "pyvrp"  # Тип солвър: "or_tools" или "pyvrp"
    algorithm: str = "or_tools"  # Основен алгоритъм. В момента се поддържа само "or_tools".

    # --- Основни параметри на търсенето ---
    time_limit_seconds: int = 480
    # Описание: Максимално време в секунди, което solver-ът има за намиране на решение.

    objective_metric: str = "time"
    # Описание: Какво минимизира solver-ът. "distance" = най-къси километри, "time" = най-кратко време по OSRM/Valhalla duration матрицата.

    first_solution_strategy: str = "PARALLEL_CHEAPEST_INSERTION"
    # Описание: Стратегия за намиране на първоначално решение. SAVINGS е по-бърза от AUTOMATIC.
    # Стойности: "AUTOMATIC", "PATH_CHEAPEST_ARC", "SAVINGS", "SWEEP", и др.

    local_search_metaheuristic: str = "GUIDED_LOCAL_SEARCH"
    # Описание: SIMULATED_ANNEALING е по-добра за избягване на локални оптимуми.
    # Стойности: "AUTOMATIC", "GUIDED_LOCAL_SEARCH", "SIMULATED_ANNEALING", "TABU_SEARCH".
    
    lns_time_limit_seconds: float = 1.0
    # Описание: Много кратък микро-лимит принуждава solver-а да се движи бързо.
    # Употреба: 0.1 секунди е достатъчно за една стъпка, но не позволява зависване.
    
    # LNS neighborhood параметри
    lns_num_nodes: int = 120
    # Описание: Брой близки възли които LNS разглежда в една стъпка.
    
    lns_num_arcs: int = 150
    # Описание: Брой скъпи дъги които LNS разглежда в една стъпка.
    
    use_full_propagation: bool = True

    log_search: bool = True
    # Описание: Дали OR-Tools да извежда детайлен лог на процеса на търсене.

    search_lambda_coefficient: float = 0.6
    # Опция за пропускане на клиенти

    allow_customer_skipping: bool = True
    # Описание: Дали solver-ът може да пропуска клиенти.
    # False = ВСИЧКИ клиенти трябва да бъдат обслужени (НЯМА пропускане).
    # True = Solver-ът може да пропусне клиенти ако е необходимо.

    distance_penalty_disjunction: int = 45000
    # Описание: Фиксирано наказание за пропускане на клиент.
    # По-голяма стойност = по-трудно пропускане на клиенти (по-малка вероятност клиент да бъде пропуснат).

    # --- Настройки за паралелна обработка ---
    enable_priority_dropping: bool = True
    # True = large close-to-depot customers get lower skip penalty/prize.
    drop_volume_weight: float = 1.0
    drop_closeness_weight: float = 1.0
    min_customer_drop_penalty: int = 45000
    max_customer_drop_penalty: int = 500000

    enable_parallel_solving: bool = False  # Keep disabled for PyVRP stability
    # Описание: Дали да се стартират няколко solver-а паралелно с различни стратегии.
    
    # --- Режим на solver-а ---
    use_simple_solver: bool = False
    # Описание: Дали да се използва опростеният solver, който точно следва OR-Tools примера.
    # True = само capacity constraints, False = всички ограничения (distance, time, stops)
    
    # --- Финален реконфигурация на маршрутите ---
    enable_final_depot_reconfiguration: bool = True
    # Описание: Дали след като OR-Tools намери решение, да се реконфигурират всички маршрути
    # да започват от депото, независимо от оригиналните стартови точки.
    # True = всички маршрути започват от депото, False = запазват оригиналните стартови точки.
    
    # --- Настройки за стартово време ---
    enable_start_time_tracking: bool = True
    # Описание: Дали да се проследява стартово време за всеки маршрут.
    # True = показва времето с натрупване от стартовото време, False = показва само времето на маршрута.
    
    global_start_time_minutes: int = 480
    # Описание: Глобално стартово време в минути от 00:00 (8:00 = 480 минути).
    # Използва се ако не е зададено стартово време за конкретен тип превозно средство.

    enable_customer_time_windows: bool = True
    # Описание: Дали solver-ите да спазват работно време на клиентите.

    customer_time_window_default_start_minutes: int = 0
    # Описание: Default начало на прозореца, когато клиентът няма работно време (0 = 00:00).

    customer_time_window_default_end_minutes: int = 1439
    # Описание: Default край на прозореца, когато клиентът няма работно време (1439 = 23:59).
    
    num_workers: int = -1
    # Описание: Брой паралелни процеси. -1 означава да се използват всички ядра без едно.

    pyvrp_seed_base: int = 1
    # Описание: Seed за PyVRP, когато pyvrp_seed е None. В паралелен режим worker-ите използват pyvrp_seed_base, pyvrp_seed_base+1...
    pyvrp_seed: Optional[int] = None
    # Описание: Ако е зададен, single mode използва точно този seed. В паралелен режим worker-ите използват pyvrp_seed, pyvrp_seed+1...

    pyvrp_num_neighbours: int = 120
    # Описание: Размер на granular neighbourhood-а на PyVRP. По-голяма стойност = по-бавно, но по-добър шанс за качество при две депа.
    pyvrp_ils_no_improvement: int = 350000
    # Описание: Брой ILS итерации без подобрение преди restart. По-високо = по-търпеливо търсене.
    pyvrp_ils_history_length: int = 500
    # Описание: Late-acceptance history length за ILS.
    pyvrp_exhaustive_on_best: bool = True
    # Описание: По-скъпо локално търсене при ново най-добро решение.
    pyvrp_use_extended_operators: bool = True
    # Описание: Добавя по-тежки PyVRP move operators (Exchange30/31/32/33, SwapStar, SwapRoutes).
    pyvrp_min_perturbations: int = 1
    pyvrp_max_perturbations: int = 40
    # Описание: Сила на perturbation при restart-и. По-високо помага да излезе от лош локален оптимум.
    pyvrp_display_progress: bool = True
    # Описание: Ако е True, PyVRP печата собствен progress output през solve().

    parallel_first_solution_strategies: List[str] = field(default_factory=lambda: [
        "PARALLEL_CHEAPEST_INSERTION",
        "SAVINGS",
        "PARALLEL_CHEAPEST_INSERTION",
        "PATH_CHEAPEST_ARC",
        "SAVINGS",
        "PARALLEL_CHEAPEST_INSERTION",
        "PARALLEL_CHEAPEST_INSERTION"
    ])
    # Описание: Списък с "First Solution" стратегии, които да се състезават в паралелен режим.

    parallel_local_search_metaheuristics: List[str] = field(default_factory=lambda: [
        "GUIDED_LOCAL_SEARCH",
        "GUIDED_LOCAL_SEARCH",
        "GUIDED_LOCAL_SEARCH",
        "GUIDED_LOCAL_SEARCH",
        "SIMULATED_ANNEALING",
        "GUIDED_LOCAL_SEARCH",
        "TABU_SEARCH"
    ])
    # Описание: Списък с "Local Search" метаевристики, които да се състезават в паралелен режим.


@dataclass
class OutputConfig:
    """Конфигурации за генериране на изходни файлове (карти, Excel отчети, графики)."""
    # Интерактивна карта
    enable_interactive_map: bool = True # Дали да се генерира HTML файл с интерактивна карта на маршрутите.
    map_output_file: str = _abs_path("C:\\Programming\\Bizant 2.0\\cvrp-ortools-optimizer\\output/interactive_map.html") # Път и име на файла за картата.
    routes_output_dir: str = _abs_path("C:\\Programming\\Bizant 2.0\\cvrp-ortools-optimizer\\output/routes") # Директория за отделните HTML карти на маршрутите.
    route_maps_upload_mode: str = "effect_upload" # disabled = не качва; legacy = старото поведение; effect_upload = качва route HTML файловете към upload endpoint.
    route_maps_upload_url: str = "https://effect.bg/dragon/hellbizante/upload-files.php" # Endpoint за качване на индивидуалните HTML карти.
    route_maps_upload_token_field: str = "pData" # POST поле за token-а при upload.
    route_maps_upload_token: str = "Effect-Bizante-Token" # Token стойност за upload endpoint-а.
    route_maps_upload_file_field: str = "files[]" # Multipart file поле. За PHP $_FILES['files'] с много файлове се използва files[].
    route_maps_upload_timeout_seconds: int = 60 # Таймаут за качване на route HTML файловете.
    map_provider: str = "osm" # Кой визуален слой да се използва: "google" или "osm".
    folium_tiles: str = "Esri.WorldStreetMap" # Фонов слой за Folium/OpenStreetMap режим. Не използва официалния OSM tile сървър.
    google_maps_api_key: str = os.environ.get("GOOGLE_MAPS_API_KEY", "") # Google Maps JavaScript API key за визуализация.
    map_zoom_level: int = 12 # Начално приближение на картата.
    show_route_colors: bool = True # Дали различните маршрути да се оцветяват в различни цветове.
    show_vehicle_info: bool = True # Дали да се показва информация за превозното средство при клик на маршрут.
    
    # Excel файлове
    enable_excel_output: bool = True # Дали да се генерира Excel CVRP отчет.
    excel_output_dir: str = _abs_path("C:\\Programming\\Bizant 2.0\\cvrp-ortools-optimizer\\output/excel") # Директория за запис на Excel отчетите.
    warehouse_excel_file: str = "warehouse_orders.xlsx" # Име на файла с необслужените клиенти (за склада).
    routes_excel_file: str = "vehicle_routes.xlsx" # Име на файла с детайли за всеки маршрут.
    efficiency_excel_file: str = "efficiency_report.xlsx" # Име на файла с отчет за ефективността.
    excel_bus_number_prefix: str = "10045010" # Префикс за номерата на бусове в Excel отчета.
    excel_bus_number_digits: int = 2 # Брой цифри след префикса: 01, 02, 03...
    
    # CSV файл с маршрути
    enable_csv_output: bool = True # Дали да се генерира CSV файл с маршрутите.
    csv_output_file: str = _abs_path("C:\\Programming\\Bizant 2.0\\cvrp-ortools-optimizer\\output/routes.csv") # Път и име на CSV файла с маршрутите.
    
    # Графики и анализи
    enable_charts: bool = True # Дали да се генерират PNG файлове с графики.
    charts_output_dir: str = _abs_path("C:\\Programming\\Bizant 2.0\\cvrp-ortools-optimizer\\output/charts") # Директория за запис на графиките.
    efficiency_chart_file: str = "efficiency_analysis.png" # Графика с анализ на ефективността.
    route_comparison_file: str = "route_comparison.png" # Графика, сравняваща маршрутите.
    volume_distribution_file: str = "volume_distribution.png" # Графика с разпределението на обемите.
    
    # Детайли в изходните файлове
    include_detailed_info: bool = True # Дали да се включва допълнителна информация в отчетите.
    show_km_info: bool = True # Дали да се показва информация за километри.
    show_time_info: bool = True # Дали да се показва информация за време.
    show_volume_info: bool = True # Дали да се показва информация за обем.


@dataclass
class LoggingConfig:
    """Конфигурации за системата за логиране."""
    log_level: str = "INFO"  # Ниво на логиране: DEBUG, INFO, WARNING, ERROR, CRITICAL.
    log_file: str = _abs_path("logs/cvrp.log") # Път до лог файла.
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s" # Формат на съобщенията в лога.
    enable_console_logging: bool = True # Дали да се печатат логове в конзолата.
    enable_file_logging: bool = True # Дали да се записват логове във файл.
    max_log_size_mb: int = 10 # Максимален размер на лог файла в мегабайти, преди да се архивира.
    backup_count: int = 5 # Брой архивирани лог файлове, които да се пазят.


@dataclass
class CacheConfig:
    """Конфигурации за системата за кеширане."""
    enable_cache: bool = False # Дали кеширането е активно.
    cache_dir: str = _abs_path("cache") # Директория, в която се съхраняват кеш файловете.
    osrm_cache_file: str = "osrm_matrix_cache.json" # Файл за кеширане на OSRM матриците с разстояния.
    routes_cache_file: str = "routes_cache.json" # Файл за кеширане на готови решения.
    cache_expiry_hours: int = 24 # Време в часове, след което кешът се счита за невалиден.
    max_cache_size_mb: int = 100 # Максимален размер на кеш директорията.


@dataclass
class PerformanceConfig:
    """Конфигурации, свързани с производителността на приложението."""
    max_concurrent_requests: int = 10 # Максимален брой едновременни заявки (напр. към OSRM).
    chunk_processing_delay: float = 0.1  # Време за изчакване в секунди между обработката на отделни "chunks".
    memory_limit_mb: int = 2048 # Ограничение на паметта (информативно, не се налага стриктно).
    enable_multiprocessing: bool = True # Дали да се използва multiprocessing за ускоряване на изчисления.
    max_workers: int = 12 # Максимален брой паралелни процеси/нишки.


@dataclass
class APIConfig:
    """Настройки за HTTP API сървъра, който приема POST заявки от други програми."""
    api_host: str = "0.0.0.0"  # 0.0.0.0 = приема заявки от други компютри в мрежата.
    api_port: int = 8088
    api_public_url: str = ""  # URL за извикване от друга програма, напр. http://10.10.100.134:8088 или https://domain.com/cvrp
    api_key: str = ""  # Ако е попълнено, /run и /solve изискват X-CVRP-API-Key или Authorization: Bearer.
    api_endpoint: str = "/solve"
    trigger_endpoint: str = "/run"  # Стартира оптимизацията с текущата конфигурация, без POST payload с клиенти.
    health_endpoint: str = "/health"


@dataclass
class SetDataConfig:
    """Настройки за връщане на готовите маршрути към Bizant чрез cmd=setData."""
    enable_set_data_upload: bool = False  # Включва изпращане на резултата към setData след успешно решение.
    set_data_url: str = "http://sio.effect.bg:7080/lubiv_Bizant"  # URL за setData endpoint.
    set_data_http_method: str = "GET"  # HTTP метод за setData: GET или POST.
    set_data_command: str = "setData"  # cmd параметър.
    set_data_done_flag: str = "1973"  # DoneFlag параметър.
    set_data_id_skld: str = "106"  # IdSkld за маршрути от основното депо.
    set_data_vratza_id_skld: str = "128"  # IdSkld за маршрути от депо Враца.
    set_data_depot_id_skld_map: str = ""  # Корекции по депо: Име=IdSkld;Име2=IdSkld2.
    set_data_id_grafik: str = ""  # IdGrafik параметър.
    set_data_id_grafik_template: str = "{bus_number}"  # Шаблон за IdGrafik. По подразбиране е номерът на буса от Excel.
    set_data_bukva_template: str = "БХ{route_number}-{stop_number}"  # Шаблон за Bukva, напр. БХ1-1.
    enable_unserved_set_data_upload: bool = True  # Дали да се изпращат и необслужените клиенти към setData.
    set_data_unserved_done_flag: str = "0"  # DoneFlag за необслужени. Празно = използва set_data_done_flag.
    set_data_unserved_id_grafik: str = "1004501000"  # IdGrafik за необслужени клиенти, ако няма шаблон.
    set_data_unserved_id_grafik_template: str = "{id_grafik}"  # Шаблон за IdGrafik на необслужени.
    set_data_unserved_bukva_template: str = "HOF-{id_plas_doc}"  # Шаблон за Bukva на необслужени клиенти.
    enable_make_group: bool = False  # Дали след успешни setData заявки да се изпрати cmd=makeGroup по склад.
    set_data_make_group_command: str = "makeGroup"  # cmd за групиране след успешни setData заявки.
    set_data_timeout_seconds: int = 30  # Таймаут за setData заявка.


@dataclass
class MainConfig:
    """Главна конфигурация, която обединява всички останали модулни конфигурации."""
    # Модулни конфигурации
    locations: LocationConfig = field(default_factory=LocationConfig)
    vehicles: Optional[List[VehicleConfig]] = None
    routing: RoutingConfig = field(default_factory=RoutingConfig)  # Избор на routing engine
    osrm: OSRMConfig = field(default_factory=OSRMConfig)
    valhalla: ValhallaConfig = field(default_factory=ValhallaConfig)  # Valhalla конфигурация
    input: InputConfig = field(default_factory=InputConfig)
    warehouse: WarehouseConfig = field(default_factory=WarehouseConfig)
    cvrp: CVRPConfig = field(default_factory=CVRPConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    api: APIConfig = field(default_factory=APIConfig)
    set_data: SetDataConfig = field(default_factory=SetDataConfig)
    
    # Глобални настройки на приложението
    debug_mode: bool = True # Включва/изключва дебъг режим с по-детайлни логове.
    verbose: bool = True # Дали да се извежда по-подробна информация в конзолата.
    dry_run: bool = False  # "Сухо" изпълнение - изпълнява се цялата логика, но без реални операции като запис на файлове.
    
    def __post_init__(self):
        """Инициализира default конфигурации за превозните средства, ако не са зададени."""
        if self.vehicles is None:
            self.vehicles = self._create_default_vehicles()
    
    def _create_default_vehicles(self) -> List[VehicleConfig]:
        """Създава стандартен set от превозни средства, ако не е дефиниран друг."""
        # --- Примерни GPS координати за различни депа ---
        depot_main = self.locations.depot_location
        depot_center = self.locations.center_location
        depot_vratza = self.locations.vratza_depot_location

        return [
            VehicleConfig(
                vehicle_type=VehicleType.INTERNAL_BUS,
                capacity=6000,
                count=11,
                name="Маршрут",
                fixed_cost=0,
                max_distance_km=None,
                max_time_hours=8,
                service_time_minutes=8,
                enabled=True,
                max_customers_per_route=None,
                start_location=(42.695785029219415, 23.23165887245312),
                start_time_minutes=480,
                tsp_depot_location=(42.695785029219415, 23.23165887245312),
                end_location=None,
            ),
            VehicleConfig(
                vehicle_type=VehicleType.CENTER_BUS,
                capacity=6000,
                count=1,
                name="Център",
                fixed_cost=0,
                max_distance_km=None,
                max_time_hours=8,
                service_time_minutes=8,
                enabled=True,
                max_customers_per_route=None,
                start_location=(42.695785029219415, 23.23165887245312),
                start_time_minutes=510,
                tsp_depot_location=(42.695785029219415, 23.23165887245312),
                end_location=None,
            ),
            VehicleConfig(
                vehicle_type=VehicleType.EXTERNAL_BUS,
                capacity=320,
                count=1,
                name="Доп. бус",
                fixed_cost=0,
                max_distance_km=None,
                max_time_hours=8,
                service_time_minutes=8,
                enabled=False,
                max_customers_per_route=None,
                start_location=(42.695785029219415, 23.23165887245312),
                start_time_minutes=450,
                tsp_depot_location=(42.695785029219415, 23.23165887245312),
                end_location=None,
            ),
            VehicleConfig(
                vehicle_type=VehicleType.SPECIAL_BUS,
                capacity=300,
                count=2,
                name="",
                fixed_cost=40000,
                max_distance_km=None,
                max_time_hours=8,
                service_time_minutes=6,
                enabled=False,
                max_customers_per_route=None,
                start_location=(42.695785029219415, 23.23165887245312),
                start_time_minutes=480,
                tsp_depot_location=(42.695785029219415, 23.23165887245312),
                end_location=None,
            ),
            VehicleConfig(
                vehicle_type=VehicleType.VRATZA_BUS,
                capacity=385,
                count=3,
                name="Враца",
                fixed_cost=0,
                max_distance_km=None,
                max_time_hours=8,
                service_time_minutes=8,
                enabled=False,
                max_customers_per_route=None,
                start_location=(43.221042895146915, 23.5344026186417),
                start_time_minutes=480,
                tsp_depot_location=(43.221042895146915, 23.5344026186417),
                end_location=None,
            ),
        ]


class ConfigManager:
    """Мениджър за зареждане и записване на конфигурации"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = _abs_path(config_file)
        self.config = MainConfig()
    
    def load_config(self, config_dict: Optional[Dict[str, Any]] = None) -> MainConfig:
        """Зарежда конфигурация от файл или речник"""
        if config_dict:
            self._update_config_from_dict(config_dict)
        elif os.path.exists(self.config_file):
            self._load_from_file()
        
        # Създаване на необходими директории
        self._create_directories()
        
        return self.config
    
    def _load_from_file(self) -> None:
        """Зарежда конфигурация от JSON файл"""
        import json
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                self._update_config_from_dict(config_data)
        except Exception as e:
            print(f"Грешка при зареждане на конфигурация: {e}")
    
    def _update_config_from_dict(self, config_dict: Dict[str, Any]) -> None:
        """Обновява конфигурацията от речник"""
        for section, values in config_dict.items():
            if hasattr(self.config, section) and isinstance(values, dict):
                section_config = getattr(self.config, section)
                for key, value in values.items():
                    if hasattr(section_config, key):
                        setattr(section_config, key, value)
    
    def save_config(self, config: Optional[MainConfig] = None) -> None:
        """Записва конфигурацията във файл"""
        if config:
            self.config = config
        
        config_dict = self._config_to_dict()
        
        import json
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
    
    def _config_to_dict(self) -> Dict[str, Any]:
        """Преобразува конфигурацията в речник"""
        result = {}
        
        for attr_name in dir(self.config):
            if attr_name.startswith('_'):
                continue
                
            attr_value = getattr(self.config, attr_name)
            
            if hasattr(attr_value, '__dict__'):
                # Dataclass обект
                result[attr_name] = {
                    k: v for k, v in attr_value.__dict__.items() 
                    if not k.startswith('_')
                }
            elif isinstance(attr_value, list):
                # Списък с dataclass обекти
                result[attr_name] = [
                    {k: v for k, v in item.__dict__.items() if not k.startswith('_')}
                    if hasattr(item, '__dict__') else item
                    for item in attr_value
                ]
            elif not callable(attr_value):
                result[attr_name] = attr_value
        
        return result
    
    def _create_directories(self) -> None:
        """Създава необходимите директории"""
        map_output_dir = self.config.output.map_output_file
        if os.path.splitext(map_output_dir)[1]:
            map_output_dir = os.path.dirname(map_output_dir)

        csv_output_dir = self.config.output.csv_output_file
        if os.path.splitext(csv_output_dir)[1]:
            csv_output_dir = os.path.dirname(csv_output_dir)

        directories = [
            os.path.dirname(self.config.input.excel_file_path),
            self.config.output.excel_output_dir,
            self.config.output.charts_output_dir,
            self.config.output.routes_output_dir,
            map_output_dir,
            csv_output_dir,
            os.path.dirname(self.config.logging.log_file),
            self.config.cache.cache_dir
        ]
        
        for directory in directories:
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
    
    def get_config(self) -> MainConfig:
        """Връща текущата конфигурация"""
        return self.config
    
    def get_enabled_vehicles(self) -> List[VehicleConfig]:
        """Връща само включените превозни средства"""
        if self.config.vehicles is None:
            return []
        return [v for v in self.config.vehicles if v.enabled]
    
    def get_total_vehicle_capacity(self) -> int:
        """Изчислява общия капацитет на всички включени превозни средства"""
        return sum(v.capacity * v.count for v in self.get_enabled_vehicles())
    
    def update_vehicle_status(self, vehicle_type: VehicleType, enabled: bool) -> None:
        """Включва/изключва определен тип превозно средство"""
        if self.config.vehicles is None:
            return
        for vehicle in self.config.vehicles:
            if vehicle.vehicle_type == vehicle_type:
                vehicle.enabled = enabled


# Глобална инстанция на конфигурацията
config_manager = ConfigManager()

# Функции за лесен достъп до конфигурациите
def get_config() -> MainConfig:
    """Връща главната конфигурация"""
    return config_manager.get_config()

def get_osrm_config() -> OSRMConfig:
    """Връща OSRM конфигурацията"""
    return config_manager.get_config().osrm

def get_vehicle_configs() -> List[VehicleConfig]:
    """Връща конфигурациите на превозните средства"""
    return config_manager.get_enabled_vehicles()

def get_locations() -> LocationConfig:
    """Връща GPS локациите"""
    return config_manager.get_config().locations 
