"""

PYVRP CVRP Solver - Алтернативен решател използвайки PyVRP библиотека

Този модул предоставя адаптер за PyVRP, който поддържа същият интерфейс като ORToolsSolver,
но използва PyVRP для решаване на Vehicle Routing Problem с Capacities (CVRP).

PyVRP е специализирана библиотека за VRP оптимизация, базирана на Iterated Local Search.

"""

from __future__ import annotations

import logging
import math
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

try:
    import pyvrp
    from pyvrp import Model, solve, SolveParams, constants
    from pyvrp.stop import MaxRuntime
    PYVRP_AVAILABLE = True
except ImportError:
    PYVRP_AVAILABLE = False
    logging.warning("PyVRP ne e instaliran. Shte se izpolzva OR-Tools.")

from config import (
    CVRPConfig,
    VehicleConfig,
    LocationConfig,
    calculate_customer_drop_penalties,
    is_location_in_center_zone,
)
from input_handler import Customer
from osrm_client import DistanceMatrix
from warehouse_manager import WarehouseAllocation
from cvrp_solver import Route, CVRPSolution, calculate_distance_km


logger = logging.getLogger(__name__)


class PyVRPSolver:
    """
    PyVRP CVRP решател - адаптер за OR-Tools интерфейс.

    Преобразува нашите структури в PyVRP формат, решава проблема и преобразува обратно.

    Атрибути:
        config: CVRPConfig с всички настройки
        vehicle_configs: Активни конфигурации на превозни средства
        customers: Списък клиенти за обслужване
        distance_matrix: OSRM матрица (разстояния/времена)
        unique_depots: Списък на GPS координати на депа
        center_zone_customers: Клиенти в централната зона
        location_config: Географски настройки
    """

    def __init__(
        self,
        config: CVRPConfig,
        vehicle_configs: List[VehicleConfig],
        customers: List[Customer],
        distance_matrix: DistanceMatrix,
        unique_depots: List[Tuple[float, float]],
        center_zone_customers: Optional[List[Customer]] = None,
        location_config: Optional[LocationConfig] = None,
    ):
        """
        Инициализира PyVRP решателя.

        Args:
            config: Настройки на решателя
            vehicle_configs: Активни превозни средства и ограничения
            customers: Клиенти за обслужване
            distance_matrix: OSRM матрица
            unique_depots: GPS координати на депа
            center_zone_customers: Клиенти в центъра
            location_config: Географски настройки
        """
        self.config = config
        self.vehicle_configs = vehicle_configs
        self.customers = customers
        self.distance_matrix = distance_matrix
        self.unique_depots = unique_depots
        self.center_zone_customers = center_zone_customers or []
        self.location_config = location_config

    def solve(self) -> CVRPSolution:
        """
        Решава CVRP проблема използвайки PyVRP.

        Returns:
            CVRPSolution със списък маршрути и метрики
        """
        if not PYVRP_AVAILABLE:
            logger.error("PyVRP is not installed")
            return self._create_empty_solution()

        try:
            # 1. Създаване на PyVRP модел
            model = self._create_pyvrp_model()
            
            logger.info(f"PyVRP model created successfully")
            logger.info(f"  - Depots: {len(model.depots)}")
            logger.info(f"  - Clients: {len(model.clients)}")
            logger.info(f"  - Vehicle types: {len(model.vehicle_types)}")

            # 2. Получаваме ProblemData от модела
            problem_data = model.data()

            # 3. Решаване
            logger.info(f"Solving with PyVRP (time limit: {self.config.time_limit_seconds}s)...")
            
            # Използваме MaxRuntime като stop критерий
            stop_criterion = MaxRuntime(self.config.time_limit_seconds)
            result = solve(problem_data, stop=stop_criterion, seed=42)

            # 4. Обработка на решението
            logger.info(f"PyVRP solution found")
            logger.info(f"  - Cost: {result.cost()}")
            logger.info(f"  - Feasible: {result.is_feasible()}")
            
            return self._extract_solution(model, problem_data, result)

        except Exception as e:
            logger.error(f"Error in PyVRP solver: {e}", exc_info=True)
            return self._create_empty_solution()

    def _create_pyvrp_model(self) -> Model:
        """
        Създава PyVRP модел от нашите структури.

        Returns:
            PyVRP Model инстанция
        """
        from config import VehicleType as ConfigVehicleType
        
        model = Model()
        
        # Всички локации (депа + клиенти) в списък за лесен достъп
        all_locations = []  # List of (depot/client objects)
        
        # 1. Добавяме депа
        depot_objects = []  # За референция към depot обекти
        depot_to_obj = {}
        logger.info(f"Adding {len(self.unique_depots)} depots to model:")
        for i, (lat, lon) in enumerate(self.unique_depots):
            # Model.add_depot приема параметри директно, връща Depot обект
            depot = model.add_depot(x=lon, y=lat, name=f"Depot_{i}")
            depot_objects.append(depot)
            depot_to_obj[(lat, lon)] = depot
            all_locations.append(depot)
            logger.info(f"  - Depot {i}: ({lat}, {lon})")

        # 2. Определяме кои клиенти са в център зоната
        center_priority_enabled = bool(
            self.location_config
            and self.location_config.enable_center_zone_priority
        )
        center_restrictions_enabled = bool(
            self.location_config
            and self.location_config.enable_center_zone_restrictions
        )
        center_zone_customer_ids = {c.id for c in self.center_zone_customers} if self.center_zone_customers else set()
        if self.location_config and (center_priority_enabled or center_restrictions_enabled):
            center_zone_customer_ids.update({
                customer.id
                for customer in self.customers
                if customer.coordinates
                and is_location_in_center_zone(customer.coordinates, self.location_config)
            })
        logger.info(f"Center zone customers: {len(center_zone_customer_ids)}")
        logger.info(
            "Center zone settings: priority=%s, restrictions=%s",
            center_priority_enabled,
            center_restrictions_enabled,
        )

        # 3. Добавяме клиенти
        enabled_vehicles = [v for v in self.vehicle_configs if v.enabled]
        service_duration_s = 0  # Добавя се vehicle-specific в edge durations.
        
        client_objects = []  # За референция към client обекти
        client_in_center = []  # Булев списък дали клиентът е в центъра
        
        # Определяме дали да позволим пропускане на клиенти
        # Prize е "наградата" за обслужване - колкото по-висока, толкова по-малко вероятно е да се пропусне
        allow_dropping = self.config.allow_customer_skipping if hasattr(self.config, 'allow_customer_skipping') else True
        drop_penalties = calculate_customer_drop_penalties(
            self.customers,
            self.unique_depots,
            self.config,
        )
        
        logger.info(
            f"Customer dropping: {'enabled' if allow_dropping else 'disabled'}, "
            f"penalty/prize range="
            f"{min(drop_penalties) if drop_penalties else 0}.."
            f"{max(drop_penalties) if drop_penalties else 0}"
        )
        
        for idx, customer in enumerate(self.customers):
            lat, lon = customer.coordinates if customer.coordinates else (0, 0)
            
            # Определяме delivery (обем на клиента)
            delivery = int(customer.volume * 100) if hasattr(customer, 'volume') and customer.volume else 1
            
            # Проверяваме дали клиентът е в център зоната
            is_in_center = customer.id in center_zone_customer_ids
            client_in_center.append(is_in_center)
            
            # Prize пропорционална на обема - по-големи клиенти са по-важни
            # Ако allow_dropping=False, клиентите са required=True
            client_prize = int(drop_penalties[idx]) if allow_dropping else 0
            
            # Model.add_client приема параметри директно
            # delivery има 2 измерения: [volume, 1 stop]
            client = model.add_client(
                x=lon,
                y=lat,
                delivery=[delivery, 1],  # [volume, 1 спирка]
                service_duration=service_duration_s,
                prize=client_prize,
                required=not allow_dropping,  # Ако allow_dropping=True, клиентите НЕ са required
                name=f"Client_{customer.id}"
            )
            client_objects.append(client)
            all_locations.append(client)
            logger.debug(f"  - Client {idx}: ID={customer.id}, volume={delivery}, center={is_in_center}, prize={client_prize}")

        # 4. Създаваме профил за всеки тип бус, за да има точен service time.
        center_discount = (
            self.location_config.discount_center_bus
            if self.location_config and center_priority_enabled
            else 1.0
        )
        center_bus_outside_penalty = (
            getattr(self.location_config, "center_bus_outside_center_penalty", 50000)
            if self.location_config
            else 50000
        )
        center_penalties_by_type = {}
        if self.location_config and center_restrictions_enabled:
            center_penalties_by_type = {
                ConfigVehicleType.INTERNAL_BUS.value: int(getattr(self.location_config, "internal_bus_center_penalty", 50000) or 0),
                ConfigVehicleType.EXTERNAL_BUS.value: int(getattr(self.location_config, "external_bus_center_penalty", 50000) or 0),
                ConfigVehicleType.SPECIAL_BUS.value: int(getattr(self.location_config, "special_bus_center_penalty", 50000) or 0),
                ConfigVehicleType.VRATZA_BUS.value: int(getattr(self.location_config, "vratza_bus_center_penalty", 50000) or 0),
            }
        
        vehicle_profiles = {}
        vehicle_service_times = {}
        profile_vehicle_configs = {}
        for v_config in enabled_vehicles:
            v_key = v_config.vehicle_type.value
            if v_key in vehicle_profiles:
                continue
            vehicle_profiles[v_key] = model.add_profile()
            vehicle_service_times[v_key] = int(v_config.service_time_minutes * 60)
            profile_vehicle_configs[v_key] = v_config
            logger.info(
                f"Added profile for {v_key}: service time {v_config.service_time_minutes} мин/клиент"
            )

        # 5. Добавяме edges за всички профили
        num_locations = len(all_locations)
        num_depots = len(depot_objects)
        logger.info(f"Adding edges with center zone logic...")
        logger.info(f"  - Center discount for CENTER_BUS: {center_discount}")
        logger.info(f"  - Outside-center penalty for CENTER_BUS: {center_bus_outside_penalty}")
        logger.info(f"  - Center penalties by bus type: {center_penalties_by_type}")
        
        # Настройки за градски трафик
        enable_traffic = False
        traffic_multiplier = 1.0
        city_center = None
        city_radius = 0
        
        if self.location_config:
            enable_traffic = getattr(self.location_config, 'enable_city_traffic_adjustment', False)
            traffic_multiplier = getattr(self.location_config, 'city_traffic_duration_multiplier', 1.0)
            city_center = getattr(self.location_config, 'city_center_coords', None)
            city_radius = getattr(self.location_config, 'city_traffic_radius_km', 12.0)
        
        if enable_traffic and city_center:
            logger.info(f"City traffic adjustment ENABLED:")
            logger.info(f"  - City center: {city_center}")
            logger.info(f"  - City radius: {city_radius} km")
            logger.info(f"  - Duration multiplier: {traffic_multiplier} (+{(traffic_multiplier-1)*100:.0f}%)")
        
        # Предварително определяме кои локации са в градската зона
        locations_in_city = []
        for loc_idx in range(num_locations):
            if loc_idx < num_depots:
                # Депо
                lat, lon = self.unique_depots[loc_idx]
            else:
                # Клиент
                client_idx = loc_idx - num_depots
                if client_idx < len(self.customers):
                    lat, lon = self.customers[client_idx].coordinates or (0, 0)
                else:
                    lat, lon = 0, 0
            
            # Проверяваме дали е в градската зона
            in_city = False
            if enable_traffic and city_center:
                dist_to_city_center = calculate_distance_km((lat, lon), city_center)
                in_city = dist_to_city_center <= city_radius
            locations_in_city.append(in_city)
        
        city_edges_count = 0
        for i in range(num_locations):
            for j in range(num_locations):
                if i == j:
                    continue
                
                # Базово разстояние и време от OSRM матрицата
                base_distance = int(self.distance_matrix.distances[i][j])
                duration = int(self.distance_matrix.durations[i][j])
                
                # Прилагаме множител за градски трафик ако и двете точки са в града
                if enable_traffic and locations_in_city[i] and locations_in_city[j]:
                    duration = int(duration * traffic_multiplier)
                    city_edges_count += 1
                
                # Проверяваме дали destination е клиент в центъра
                is_dest_center_client = False
                if j >= num_depots:
                    client_idx = j - num_depots
                    if client_idx < len(client_in_center):
                        is_dest_center_client = client_in_center[client_idx]
                
                # Базов edge (без профил) - без service time.
                model.add_edge(
                    frm=all_locations[i],
                    to=all_locations[j],
                    distance=base_distance,
                    duration=duration
                )

                for v_key, v_config in profile_vehicle_configs.items():
                    vehicle_duration = duration
                    if i >= num_depots:
                        vehicle_duration += vehicle_service_times.get(v_key, 15 * 60)

                    if v_config.vehicle_type == ConfigVehicleType.CENTER_BUS:
                        if is_dest_center_client and center_priority_enabled:
                            vehicle_distance = int(base_distance * center_discount)
                        elif center_priority_enabled:
                            vehicle_distance = base_distance + int(center_bus_outside_penalty)
                        else:
                            vehicle_distance = base_distance
                    else:
                        if is_dest_center_client and center_restrictions_enabled:
                            vehicle_penalty = center_penalties_by_type.get(v_config.vehicle_type.value, 50000)
                            vehicle_distance = base_distance + int(vehicle_penalty)
                        else:
                            vehicle_distance = base_distance

                    model.add_edge(
                        frm=all_locations[i],
                        to=all_locations[j],
                        distance=vehicle_distance,
                        duration=vehicle_duration,
                        profile=vehicle_profiles[v_key]
                    )

        if enable_traffic:
            locations_in_city_count = sum(locations_in_city)
            logger.info(f"City traffic adjustment applied:")
            logger.info(f"  - Locations in city: {locations_in_city_count}/{num_locations}")
            logger.info(f"  - Edges with traffic multiplier: {city_edges_count}")

        # 6. Добавяме типове превозни средства с правилния профил
        vehicle_id = 0
        for v_config in self.vehicle_configs:
            if not v_config.enabled:
                continue

            # Определяме депо за този тип превозно средство
            start_depot = None
            if v_config.start_location:
                # Търсим депото по координати
                if v_config.start_location in depot_to_obj:
                    start_depot = depot_to_obj[v_config.start_location]
                else:
                    # Може да има малки разлики във float стойностите
                    # Търсим най-близкото депо
                    for depot_coords, depot_obj in depot_to_obj.items():
                        if (abs(depot_coords[0] - v_config.start_location[0]) < 0.0001 and 
                            abs(depot_coords[1] - v_config.start_location[1]) < 0.0001):
                            start_depot = depot_obj
                            break
            
            if start_depot is None:
                start_depot = depot_objects[0] if depot_objects else None
                if v_config.start_location:
                    logger.warning(f"⚠️ Depot {v_config.start_location} not found for {v_config.vehicle_type.value}, using default depot")

            # Преобразуваме ограниченията
            capacity = int(v_config.capacity * 100)
            max_distance = int(v_config.max_distance_km * 1000) if v_config.max_distance_km else constants.MAX_VALUE
            max_time = int(v_config.max_time_hours * 3600)  # в секунди
            fixed_cost = int(getattr(v_config, "fixed_cost", 0) or 0)
            
            # Max customers per route (ако не е зададено, използваме голямо число)
            max_customers = v_config.max_customers_per_route if v_config.max_customers_per_route else 1000

            vehicle_profile = vehicle_profiles[v_config.vehicle_type.value]
            profile_name = f"{v_config.vehicle_type.value} ({v_config.service_time_minutes} мин service)"

            # Model.add_vehicle_type с двумерен capacity: [volume, max_stops]
            model.add_vehicle_type(
                num_available=v_config.count,
                capacity=[capacity, max_customers],  # [volume_capacity, max_customers]
                start_depot=start_depot,
                end_depot=start_depot,
                max_distance=max_distance,
                shift_duration=max_time,
                fixed_cost=fixed_cost,
                profile=vehicle_profile,
                name=f"{v_config.vehicle_type.value}"
            )
            
            logger.info(f"Vehicle type: {v_config.vehicle_type.value}")
            logger.info(f"  - Count: {v_config.count}")
            logger.info(f"  - Capacity: {v_config.capacity}")
            logger.info(f"  - Max customers: {max_customers}")
            logger.info(f"  - Max distance: {v_config.max_distance_km}km")
            logger.info(f"  - Max time: {v_config.max_time_hours}h")
            logger.info(f"  - Fixed cost: {fixed_cost}")
            logger.info(f"  - Profile: {profile_name}")
            logger.info(f"  - Depot: {start_depot}")

            vehicle_id += 1

        logger.info(f"PyVRP model created successfully")
        logger.info(f"  - Depots: {len(model.depots)}")
        logger.info(f"  - Clients: {len(model.clients)}")
        logger.info(f"  - Vehicle types: {len(model.vehicle_types)}")
        logger.info(f"  - Center zone customers: {len(center_zone_customer_ids)}")

        return model


    def _extract_solution(self, model: Model, problem_data, result) -> CVRPSolution:
        """
        Преобразува PyVRP решение в нашия CVRPSolution формат.

        Args:
            model: PyVRP Model
            problem_data: PyVRP ProblemData (model.data())
            result: PyVRP Result

        Returns:
            CVRPSolution
        """
        routes_list = []
        dropped_customers = set(c.id for c in self.customers)
        total_distance_km = 0.0
        total_time_minutes = 0.0
        total_volume = 0.0

        if not result.best or not result.best.routes():
            logger.warning("PyVRP vrati praznoto reshenie")
            return self._create_empty_solution()
        
        # Вземаме списъка с vehicle types за да можем да ги индексираме
        vehicle_types_list = problem_data.vehicle_types()

        # Iteriraem prez vseki marshut v resheniyeto
        for route in result.best.routes():
            route_customers = []
            route_distance_m = 0.0
            route_time_s = 0.0
            route_volume = 0.0

            # Izvlichame klientite v marshuta
            # route.visits() e spisuk na indeksi na poseshheniya
            try:
                visits = route.visits()
            except:
                continue
            
            for visit_idx in visits:
                # Opredelyame koy e tozi indeks
                num_depots = len(model.depots)
                
                if visit_idx < num_depots:
                    # Tova e depo - preskachame
                    continue
                
                # Tova e klient
                client_idx = visit_idx - num_depots
                if client_idx < len(self.customers):
                    customer = self.customers[client_idx]
                    route_customers.append(customer)
                    dropped_customers.discard(customer.id)
                    route_volume += customer.volume if hasattr(customer, 'volume') and customer.volume else 0

            # Izchislyavaem razstoyanie i vreme za marshuta
            if route_customers:
                # Namirame depoto za tozi marshut чрез vehicle_type
                try:
                    vt_index = route.vehicle_type()  # Връща индекс (int)
                    vt = vehicle_types_list[vt_index] if vt_index < len(vehicle_types_list) else None
                    # vt.start_depot е индекс на депото в модела
                    depot_idx = vt.start_depot if vt else 0
                except Exception as e:
                    logger.warning(f"Could not get depot from route: {e}")
                    depot_idx = 0
                
                if depot_idx < len(self.unique_depots):
                    depot_location = self.unique_depots[depot_idx]
                else:
                    depot_location = self.unique_depots[0]

                # Opredelyame tipa prevozno sredstvo ot marshuta
                vehicle_type = self._get_vehicle_type_from_route(route, vehicle_types_list)
                vehicle_config = self._get_vehicle_config_for_type(vehicle_type)

                # Izchislyavaem tochnoto vreme i razstoyanie
                route_distance_m, route_time_s = self._calculate_route_metrics(
                    route_customers, depot_location, vehicle_config
                )
                
                total_distance_km += route_distance_m / 1000
                total_time_minutes += route_time_s / 60
                total_volume += route_volume

                route_obj = Route(
                    vehicle_type=vehicle_type,
                    vehicle_id=0,
                    customers=route_customers,
                    depot_location=depot_location,
                    total_distance_km=route_distance_m / 1000,
                    total_time_minutes=route_time_s / 60,
                    total_volume=route_volume,
                    is_feasible=True
                )
                routes_list.append(route_obj)

        # Sobiramy propusnatite klienti
        dropped_customers_list = [c for c in self.customers if c.id in dropped_customers]

        # Sazdavaame resheniyeto
        solution = CVRPSolution(
            routes=routes_list,
            dropped_customers=dropped_customers_list,
            total_distance_km=total_distance_km,
            total_time_minutes=total_time_minutes,
            total_vehicles_used=len(routes_list),
            fitness_score=total_distance_km,
            is_feasible=result.is_feasible(),
            total_served_volume=total_volume
        )

        logger.info(f"Reshenie obraboteno:")
        logger.info(f"  - Marshuti: {len(routes_list)}")
        logger.info(f"  - Propushcheni klienti: {len(dropped_customers_list)}")
        logger.info(f"  - Obshto razstoyanie: {total_distance_km:.1f} km")
        logger.info(f"  - Obshto vreme: {total_time_minutes:.1f} min")
        logger.info(f"  - Obsluzhden obem: {total_volume:.1f}")

        return solution

    def _calculate_route_metrics(
        self,
        customers: List[Customer],
        depot_location: Tuple[float, float],
        vehicle_config: Optional[VehicleConfig] = None,
    ) -> Tuple[float, float]:
        """
        Изчислява разстояние и време за маршрут.

        Args:
            customers: Списък с клиенти в маршрута
            depot_location: GPS координати на депото

        Returns:
            Кортеж (разстояние_м, време_сек)
        """
        if not customers:
            return 0.0, 0.0

        total_distance = 0.0
        total_time = 0.0

        # Намираме индекса на депото в матрицата
        depot_index = None
        for i, depot in enumerate(self.unique_depots):
            if depot == depot_location:
                depot_index = i
                break

        if depot_index is None:
            logger.warning(f"⚠️ Депо {depot_location} не намерено, използвам главното")
            depot_index = 0

        # От депо до първия клиент
        current_node = depot_index
        
        if vehicle_config:
            service_time_s = vehicle_config.service_time_minutes * 60
        else:
            service_time_s = 15 * 60

        # === ГРАДСКИ ТРАФИК: Настройки и определяне кои локации са в града ===
        enable_traffic = False
        traffic_multiplier = 1.0
        city_center = None
        city_radius = 0
        
        if self.location_config:
            enable_traffic = getattr(self.location_config, 'enable_city_traffic_adjustment', False)
            traffic_multiplier = getattr(self.location_config, 'city_traffic_duration_multiplier', 1.0)
            city_center = getattr(self.location_config, 'city_center_coords', None)
            city_radius = getattr(self.location_config, 'city_traffic_radius_km', 12.0)
        
        # Предварително определяме кои локации са в градската зона
        num_locations = len(self.distance_matrix.distances)
        locations_in_city = []
        for loc_idx in range(num_locations):
            if loc_idx < len(self.unique_depots):
                lat, lon = self.unique_depots[loc_idx]
            else:
                client_idx = loc_idx - len(self.unique_depots)
                if client_idx < len(self.customers):
                    lat, lon = self.customers[client_idx].coordinates or (0, 0)
                else:
                    lat, lon = 0, 0
            
            in_city = False
            if enable_traffic and city_center:
                dist_to_city_center = calculate_distance_km((lat, lon), city_center)
                in_city = dist_to_city_center <= city_radius
            locations_in_city.append(in_city)

        for customer in customers:
            # Намираме индекса на клиента в матрицата
            try:
                customer_matrix_idx = len(self.unique_depots) + self.customers.index(customer)
            except ValueError:
                logger.warning(f"⚠️ Клиент {customer.id} не намерен в списъка")
                continue

            # Добавяме разстояние
            total_distance += self.distance_matrix.distances[current_node][customer_matrix_idx]
            
            # Добавяме време с трафик корекция
            travel_time = self.distance_matrix.durations[current_node][customer_matrix_idx]
            if enable_traffic and current_node < len(locations_in_city) and customer_matrix_idx < len(locations_in_city):
                if locations_in_city[current_node] and locations_in_city[customer_matrix_idx]:
                    travel_time = travel_time * traffic_multiplier
            total_time += travel_time
            total_time += service_time_s  # vehicle-specific service time

            current_node = customer_matrix_idx

        # От последния клиент обратно в депото
        total_distance += self.distance_matrix.distances[current_node][depot_index]
        travel_time_back = self.distance_matrix.durations[current_node][depot_index]
        if enable_traffic and current_node < len(locations_in_city) and depot_index < len(locations_in_city):
            if locations_in_city[current_node] and locations_in_city[depot_index]:
                travel_time_back = travel_time_back * traffic_multiplier
        total_time += travel_time_back

        return total_distance, total_time

    def _get_vehicle_config_for_type(self, vehicle_type) -> Optional[VehicleConfig]:
        vehicle_type_value = getattr(vehicle_type, "value", str(vehicle_type))
        for v_config in self.vehicle_configs:
            if v_config.enabled and v_config.vehicle_type.value == vehicle_type_value:
                return v_config
        for v_config in self.vehicle_configs:
            if v_config.enabled:
                return v_config
        return None

    def _get_vehicle_type_from_route(self, route, vehicle_types_list) -> str:
        """
        Определя типа превозно средство на маршрута.

        Args:
            route: PyVRP Route
            vehicle_types_list: Списък с VehicleType обекти от problem_data.vehicle_types()

        Returns:
            VehicleType стойност
        """
        from config import VehicleType as ConfigVehicleType
        
        try:
            # PyVRP Route има vehicle_type() метод който връща ИНДЕКС (int)
            vt_index = route.vehicle_type()
            
            if isinstance(vt_index, int) and vt_index < len(vehicle_types_list):
                vt = vehicle_types_list[vt_index]
                vt_name = vt.name
                
                # Търсим съответния VehicleType enum
                for config_vt in ConfigVehicleType:
                    if config_vt.value == vt_name:
                        return config_vt
                
                # Ако не намерим точно съвпадение, връщаме името като string
                return vt_name
        except Exception as e:
            logger.warning(f"Could not get vehicle type from route: {e}")
        
        # По подразбиране връщаме първия enabled vehicle type
        for v_config in self.vehicle_configs:
            if v_config.enabled:
                return v_config.vehicle_type
        
        # Fallback
        return ConfigVehicleType.INTERNAL_BUS

    def _create_empty_solution(self) -> CVRPSolution:
        """
        Създава празно решение (всички клиенти пропуснати).

        Returns:
            CVRPSolution без маршрути
        """
        return CVRPSolution(
            routes=[],
            dropped_customers=self.customers,
            total_distance_km=0.0,
            total_time_minutes=0.0,
            total_vehicles_used=0,
            fitness_score=float('inf'),
            is_feasible=False,
            total_served_volume=0.0
        )


class PyVRPSolverWrapper:
    """
    Wrapper за PyVRPSolver, съвместим с ORToolsSolver интерфейс.
    """

    def __init__(self, config: CVRPConfig = None, location_config: LocationConfig = None):
        """
        Инициализира wrapper-а.

        Args:
            config: CVRPConfig (опционално)
        """
        self.config = config
        self.location_config = location_config

    def solve(
        self,
        allocation: WarehouseAllocation,
        depot_location: Tuple[float, float],
        distance_matrix: DistanceMatrix
    ) -> CVRPSolution:
        """
        Решава CVRP проблема използвайки PyVRP.

        Args:
            allocation: WarehouseAllocation с клиенти
            depot_location: GPS координати на главното депо
            distance_matrix: OSRM матрица

        Returns:
            CVRPSolution
        """
        from config import get_config

        full_config = get_config()
        
        if self.config is None:
            self.config = full_config

        enabled_vehicles = full_config.vehicles or []
        location_config = self.location_config or full_config.locations

        # Събираме всички депа
        unique_depots = {depot_location}
        for vehicle_config in enabled_vehicles:
            if vehicle_config.enabled and vehicle_config.start_location:
                unique_depots.add(vehicle_config.start_location)

        # Главното депо винаги първо
        sorted_depots = [depot_location]
        other_depots = sorted([d for d in unique_depots if d != depot_location], key=lambda x: (x[0], x[1]))
        sorted_depots.extend(other_depots)

        # Създаваме и решаваме
        solver = PyVRPSolver(
            self.config,
            enabled_vehicles,
            allocation.vehicle_customers,
            distance_matrix,
            sorted_depots,
            allocation.center_zone_customers,
            location_config
        )

        return solver.solve()


# Удобна функция
def solve_cvrp_pyvrp(
    allocation: WarehouseAllocation,
    depot_location: Tuple[float, float],
    distance_matrix: DistanceMatrix,
    config: CVRPConfig = None,
    location_config: LocationConfig = None,
) -> CVRPSolution:
    """
    Удобна функция за решаване на CVRP използвайки PyVRP.

    Args:
        allocation: WarehouseAllocation
        depot_location: GPS координати на депо
        distance_matrix: OSRM матрица
        config: CVRPConfig (опционално)

    Returns:
        CVRPSolution
    """
    solver = PyVRPSolverWrapper(config, location_config)
    return solver.solve(allocation, depot_location, distance_matrix)
