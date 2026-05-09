"""
OSRM клиент за изчисляване на разстояния и време за пътуване
Поддържа chunking по 90 заявки и кеширане на резултатите
"""

import requests
import json
import time
import hashlib
import os
from typing import List, Dict, Tuple, Optional, Union
from dataclasses import dataclass
import logging

from datetime import datetime, timedelta
from config import get_config, OSRMConfig
from input_handler import Customer
from tqdm import tqdm

logger = logging.getLogger(__name__)


@dataclass
class DistanceMatrix:
    """Клас за матрица с разстояния и времена"""
    distances: List[List[float]]  # в метри
    durations: List[List[float]]  # в секунди
    locations: List[Tuple[float, float]]  # координати
    sources: List[int]  # индекси на източниците
    destinations: List[int]  # индекси на дестинациите


@dataclass
class RouteInfo:
    """Информация за маршрут между две точки"""
    distance_km: float
    duration_minutes: float
    source_index: int
    destination_index: int


class OSRMCache:
    """Кеш за OSRM заявки"""
    
    def __init__(self, cache_file: str, expiry_hours: int = 24):
        self.cache_file = cache_file
        self.expiry_hours = expiry_hours
        self.cache_data = self._load_cache()
    
    def _load_cache(self) -> Dict:
        """Зарежда кеша от файл"""
        if not os.path.exists(self.cache_file):
            return {}
        
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                
            # Почистване на изтекли записи
            current_time = datetime.now()
            valid_cache = {}
            
            for key, value in cache_data.items():
                if 'timestamp' in value:
                    cached_time = datetime.fromisoformat(value['timestamp'])
                    if current_time - cached_time < timedelta(hours=self.expiry_hours):
                        valid_cache[key] = value
            
            return valid_cache
            
        except Exception as e:
            logger.warning(f"Грешка при зареждане на кеш: {e}")
            return {}
    
    def _save_cache(self) -> None:
        """Записва кеша във файл"""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Грешка при записване на кеш: {e}")
    
    def _generate_key(self, locations: List[Tuple[float, float]], 
                     sources: Optional[List[int]] = None, 
                     destinations: Optional[List[int]] = None) -> str:
        """Генерира уникален ключ за кеша"""
        data_str = json.dumps({
            'locations': locations,
            'sources': sources,
            'destinations': destinations
        }, sort_keys=True)
        
        return hashlib.md5(data_str.encode()).hexdigest()
    
    def get(self, locations: List[Tuple[float, float]], 
            sources: Optional[List[int]] = None, 
            destinations: Optional[List[int]] = None) -> Optional[DistanceMatrix]:
        """Търси в кеша"""
        key = self._generate_key(locations, sources, destinations)
        
        if key in self.cache_data:
            try:
                cached_item = self.cache_data[key]
                return DistanceMatrix(
                    distances=cached_item['distances'],
                    durations=cached_item['durations'],
                    locations=cached_item['locations'],
                    sources=cached_item['sources'],
                    destinations=cached_item['destinations']
                )
            except Exception as e:
                logger.warning(f"Грешка при четене от кеш: {e}")
                del self.cache_data[key]
        
        return None
    
    def set(self, matrix: DistanceMatrix, 
            sources: Optional[List[int]] = None, 
            destinations: Optional[List[int]] = None) -> None:
        """Записва в кеша"""
        key = self._generate_key(matrix.locations, sources, destinations)
        
        self.cache_data[key] = {
            'distances': matrix.distances,
            'durations': matrix.durations,
            'locations': matrix.locations,
            'sources': matrix.sources,
            'destinations': matrix.destinations,
            'timestamp': datetime.now().isoformat()
        }
        
        self._save_cache()

    def get_complete_central_matrix(self) -> Optional[DistanceMatrix]:
        """Зарежда цялата централна матрица от кеша (ако има)"""
        if not self.cache_data:
            return None
            
        # Намираме най-голямата матрица в кеша (най-много локации)
        largest_matrix = None
        max_locations = 0
        
        for key, cached_item in self.cache_data.items():
            try:
                locations_count = len(cached_item.get('locations', []))
                if locations_count > max_locations:
                    max_locations = locations_count
                    largest_matrix = DistanceMatrix(
                        distances=cached_item['distances'],
                        durations=cached_item['durations'],
                        locations=cached_item['locations'],
                        sources=cached_item['sources'],
                        destinations=cached_item['destinations']
                    )
            except Exception as e:
                logger.warning(f"Грешка при четене на кеширан запис: {e}")
                continue
        
        if largest_matrix:
            logger.info(f"💾 Заредена централна матрица с {max_locations} локации от кеша")
        
        return largest_matrix
    
    def extract_submatrix(self, central_matrix: DistanceMatrix, 
                         required_locations: List[Tuple[float, float]]) -> Optional[DistanceMatrix]:
        """Извлича подматрица от централната матрица за дадените локации"""
        if not central_matrix or not required_locations:
            return None
            
        # Намираме индексите на нужните локации в централната матрица
        location_indices = []
        tolerance = 0.00001  # толеранс за сравнение на координати
        
        for req_loc in required_locations:
            found_index = None
            for i, central_loc in enumerate(central_matrix.locations):
                if (abs(central_loc[0] - req_loc[0]) < tolerance and 
                    abs(central_loc[1] - req_loc[1]) < tolerance):
                    found_index = i
                    break
            
            if found_index is None:
                logger.warning(f"Координата {req_loc} не е намерена в централната матрица")
                return None
            
            location_indices.append(found_index)
        
        # Създаваме подматрицата
        n = len(location_indices)
        sub_distances = [[0.0 for _ in range(n)] for _ in range(n)]
        sub_durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        for i in range(n):
            for j in range(n):
                central_i = location_indices[i]
                central_j = location_indices[j]
                sub_distances[i][j] = central_matrix.distances[central_i][central_j]
                sub_durations[i][j] = central_matrix.durations[central_i][central_j]
        
        logger.info(f"📊 Извлечена подматрица {n}x{n} от централната матрица")
        
        return DistanceMatrix(
            distances=sub_distances,
            durations=sub_durations,
            locations=required_locations,
            sources=list(range(n)),
            destinations=list(range(n))
        )


class OSRMClient:
    """Клиент за OSRM API"""
    
    def __init__(self, config: Optional[OSRMConfig] = None):
        self.config = config or get_config().osrm
        self.routing_config = get_config().routing
        self.cache = OSRMCache(
            cache_file=os.path.join(get_config().cache.cache_dir, get_config().cache.osrm_cache_file),
            expiry_hours=self.config.cache_expiry_hours
        ) if self.config.use_cache and not self._curbside_enabled() else None

        if self.config.use_cache and self._curbside_enabled():
            logger.info("OSRM кешът е пропуснат, защото curbside routing е включен.")
        
        # Оптимизиран HTTP session за по-бързи заявки
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CVRP-Optimizer/1.0',
            'Connection': 'keep-alive'
        })
        
        # HTTP connection pooling за ускорение
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # Retry стратегия
        retry_strategy = Retry(
            total=1,  # Максимум 1 retry за скорост
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=0.01  # Кратка пауза
        )
        
        # HTTP adapter с connection pooling
        adapter = HTTPAdapter(
            pool_connections=20,  # До 20 connection pool-а
            pool_maxsize=20,      # До 20 connections per pool
            max_retries=retry_strategy
        )
        
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _curbside_enabled(self) -> bool:
        return bool(getattr(self.routing_config, "enable_curbside_approach", False))

    def _approaches_query(self, count: int) -> str:
        if not self._curbside_enabled():
            return ""
        return "&approaches=" + ";".join(["curb"] * count)

    def _build_route_url(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        base_url: Optional[str] = None,
    ) -> str:
        lat1, lon1 = origin
        lat2, lon2 = destination
        clean_base_url = (base_url or self.config.base_url).rstrip("/")
        route_url = (
            f"{clean_base_url}/route/v1/{self.config.profile}/"
            f"{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
            "?overview=false&steps=false"
        )
        return route_url + self._approaches_query(2)
    
    def get_distance_matrix(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Получава матрица с разстояния използвайки интелигентен batch подход"""
        n_locations = len(locations)
        logger.info(f"🚗 Стартирам matrix заявка за {n_locations} локации")
        
        # Проверяваме кеша първо
        if self.cache:
            cached_matrix = self.cache.get(locations)
            if cached_matrix:
                logger.info(f"💾 Намерих кеширани данни за {n_locations} локации")
                return cached_matrix
        
        # Интелигентен подход:
        # 1. За малки datasets (≤30) - директно Table API
        # 2. За средни datasets (31-500) - batch Table API chunks  
        # 3. За големи datasets (>500) - паралелни Route API заявки
        
        if n_locations <= 30:
            logger.info(f"🔄 Малък dataset: използвам директно Table API за {n_locations} локации")
            try:
                return self._try_table_api_direct(locations)
            except Exception as e:
                logger.warning(f"Table API неуспешен: {e}")
                logger.info(f"🛣️ Fallback към Route API за {n_locations} локации")
                return self._build_matrix_via_routes_only(locations)
        
        elif n_locations <= 500:
            logger.info(f"🧩 Среден dataset: използвам оптимизиран batch Table API за {n_locations} локации")
            try:
                # Използваме директно оптимизирания batch Table API с 80 координати
                return self._build_optimized_table_batches(locations)
            except Exception as e:
                logger.warning(f"Оптимизиран batch Table API неуспешен: {e}")
                logger.info(f"🛣️ Fallback към паралелни Route API заявки за {n_locations} локации")
                return self._build_matrix_via_routes_only(locations)
        
        else:
            logger.info(f"🚀 Голям dataset: използвам паралелни Route API заявки за {n_locations} локации")
            return self._build_matrix_via_routes_only(locations)
    
    def _try_table_api_with_fallback(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Опитва Table API първо с локален, после с публичен сървър"""
        try:
            # Първо опитваме локалния сървър
            return self._try_table_api_to_server(locations, self.config.base_url)
        except Exception as e:
            logger.warning(f"Локален OSRM неуспешен: {e}")
            
            # Fallback към публичния сървър
            if self.config.fallback_to_public:
                logger.info(f"🌐 Fallback към публичен OSRM за {len(locations)} локации")
                try:
                    return self._try_table_api_to_server(locations, self.config.public_osrm_url)
                except Exception as e2:
                    logger.warning(f"Публичен OSRM също неуспешен: {e2}")
                    raise e2
            else:
                raise e
    
    def _try_table_api_to_server(self, locations: List[Tuple[float, float]], server_url: str) -> DistanceMatrix:
        """Опитва Table API към конкретен сървър"""
        try:
            # Опитваме GET първо за малки datasets - увеличен лимит за 80 координати
            test_url = self._build_matrix_url(locations, server_url)
            if len(test_url) <= 4000:  # Намален лимит за batch-ове от 30 координати
                response = self.session.get(test_url, timeout=10)
            else:
                # POST за по-дълги URL-и
                response = self._make_post_request(locations, server_url)
            
            response.raise_for_status()
            data = response.json()
            
            if data['code'] != 'Ok':
                raise Exception(f"OSRM грешка: {data.get('message', 'Неизвестна грешка')}")
            
            server_type = "локален" if "localhost" in server_url else "публичен"
            logger.info(f"✅ Успешен Table API към {server_type} сървър за {len(locations)} локации")
            
            # Проверка на отговора за диагностика
            logger.info(f"OSRM отговор ключове: {list(data.keys())}")
            has_distances = 'distances' in data
            has_durations = 'durations' in data
            logger.info(f"OSRM връща разстояния: {has_distances}, времена: {has_durations}")
            
            # Обработка на данните
            distances = data.get('distances', [])
            durations = data.get('durations', [])
            
            if not distances and durations:
                # Изчисляваме разстояния от времето с подобрена точност
                distances = []
                # Фактор за превръщане: средна скорост + корекционен фактор
                speed_factor = self.config.average_speed_kmh * 1000 / 3600  # m/s
                correction_factor = 1.1  # Разстоянията са по-дълги от идеалната права
                
                for i, row in enumerate(durations):
                    dist_row = []
                    for j, duration in enumerate(row):
                        if i == j:
                            # Разстоянието до себе си е 0
                            dist_row.append(0.0)
                        elif duration > 0:
                            # Реалистично разстояние: време * средна скорост * корекция
                            approx_distance = duration * speed_factor * correction_factor
                            dist_row.append(approx_distance)
                        else:
                            # Ако има проблем с времето, използваме haversine за приблизителна оценка
                            haversine_dist = self._haversine_distance(locations[i], locations[j]) * 1.3
                            dist_row.append(haversine_dist)
                    distances.append(dist_row)
                logger.info("⚠️ OSRM не връща разстояния! Изчислени разстояния от времето с повишена точност.")
            
            matrix = DistanceMatrix(
                distances=distances,
                durations=durations,
                locations=locations,
                sources=list(range(len(locations))),
                destinations=list(range(len(locations)))
            )
            
            # Записваме в кеша
            if self.cache:
                self.cache.set(matrix)
            
            return matrix
            
        except Exception as e:
            server_type = "локален" if "localhost" in server_url else "публичен"
            logger.warning(f"Table API към {server_type} сървър неуспешен: {e}")
            raise e
    
    def _try_table_api_direct(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Опитва директно Table API за малки datasets (wrapper за съвместимост)"""
        return self._try_table_api_to_server(locations, self.config.base_url)
    
    def _build_matrix_via_table_batches(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Изгражда матрица използвайки Table API batches с правилна логика"""
        n = len(locations)
        
        # Променяме логиката - използваме пряко малки batch-ове, не опитваме пълната матрица
        logger.info(f"🧩 Започвам batch Table API: {n} локации с малки batch-ове")
        
        # Директно към малките batch-ове без да опитваме пълната матрица
        return self._build_matrix_via_small_batches(locations)
    
    def _build_matrix_via_small_batches(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Fallback метод за малки batch заявки когато пълната матрица не работи"""
        n = len(locations)
        batch_size = 50  # Намален размер до 30 координати за стабилност
        
        logger.info(f"🔧 Започвам малки batch заявки: {n} локации с batches от {batch_size}")
        
        # Инициализация на резултатни матрици  
        full_distances = [[0.0 for _ in range(n)] for _ in range(n)]
        full_durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        successful_batches = 0
        failed_batches = 0
        
        # Изчисляваме брой batches
        num_batches = (n + batch_size - 1) // batch_size
        total_batch_requests = num_batches * num_batches
        
        logger.info(f"📊 Ще направя {total_batch_requests} малки Table API batch заявки")
        
        # Прогрес бар за batch заявките
        with tqdm(total=total_batch_requests, desc="🔧 Малки Table API batches", unit="batch") as pbar:
            
            # Обработка на batches като квадратни субматрици
            for i in range(0, n, batch_size):
                end_i = min(i + batch_size, n)
                sources_batch = locations[i:end_i]
                
                for j in range(0, n, batch_size):
                    end_j = min(j + batch_size, n)
                    destinations_batch = locations[j:end_j]
                    
                    try:
                        # НОВA ЛОГИКА: Използваме директно по-малки batch-ове без комбиниране
                        # Проверяваме дали source и destination batch-овете се застъпват
                        if i == j:
                            # Същия блок - използваме директно локациите
                            batch_locations = sources_batch
                        else:
                            # Различни блокове - комбинираме внимателно
                            all_batch_locations = []
                            location_map = {}
                            
                            # Добавяме source локации
                            for loc in sources_batch:
                                if loc not in location_map:
                                    location_map[loc] = len(all_batch_locations)
                                    all_batch_locations.append(loc)
                            
                            # Добавяме destination локации само ако не надвишават лимита
                            for loc in destinations_batch:
                                if loc not in location_map and len(all_batch_locations) < 30:  # Намален лимит до 30
                                    location_map[loc] = len(all_batch_locations)
                                    all_batch_locations.append(loc)
                            
                            batch_locations = all_batch_locations
                        
                        # Финална проверка за размер
                        if len(batch_locations) > 30:  # Намален лимит до 30 координати
                            logger.debug(f"Прескачам batch {i}-{end_i}, {j}-{end_j}: {len(batch_locations)} локации")
                            # Използваме Route API директно за този batch
                            self._fill_batch_with_routes(sources_batch, destinations_batch, 
                                                       full_distances, full_durations, i, j)
                            failed_batches += 1
                        else:
                            # Table API заявка за този batch
                            batch_matrix = self._try_table_api_with_fallback(batch_locations)
                            
                            # Копиране на резултатите в пълната матрица
                            if i == j:
                                # Същия блок - директно копиране
                                for src_idx in range(len(sources_batch)):
                                    for dest_idx in range(len(sources_batch)):
                                        global_src = i + src_idx
                                        global_dest = j + dest_idx
                                        full_distances[global_src][global_dest] = batch_matrix.distances[src_idx][dest_idx]
                                        full_durations[global_src][global_dest] = batch_matrix.durations[src_idx][dest_idx]
                            else:
                                # Различни блокове - mapване
                                for src_idx, src_loc in enumerate(sources_batch):
                                    for dest_idx, dest_loc in enumerate(destinations_batch):
                                        global_src = i + src_idx
                                        global_dest = j + dest_idx
                                        
                                        if src_loc in location_map and dest_loc in location_map:
                                            batch_src_idx = location_map[src_loc]
                                            batch_dest_idx = location_map[dest_loc]
                                            full_distances[global_src][global_dest] = batch_matrix.distances[batch_src_idx][batch_dest_idx]
                                            full_durations[global_src][global_dest] = batch_matrix.durations[batch_src_idx][batch_dest_idx]
                                        else:
                                            # Fallback към Route API за тази конкретна двойка
                                            try:
                                                route_matrix = self._single_route_request(src_loc, dest_loc)
                                                full_distances[global_src][global_dest] = route_matrix['distance']
                                                full_durations[global_src][global_dest] = route_matrix['duration']
                                            except:
                                                approx_distance = self._haversine_distance(src_loc, dest_loc) * 1.3
                                                full_distances[global_src][global_dest] = approx_distance
                                                full_durations[global_src][global_dest] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                            
                            successful_batches += 1
                        
                        # Обновяване на прогрес бара
                        pbar.update(1)
                        pbar.set_postfix({
                            'успешни': successful_batches,
                            'неуспешни': failed_batches,
                            'размер': f'{len(batch_locations)} loc'
                        })
                        
                        # По-малка пауза за по-бързо изпълнение с по-големи batch-ове
                        time.sleep(0.01)
                        
                    except Exception as e:
                        logger.warning(f"Table batch {i}-{end_i}, {j}-{end_j} неуспешен: {e}")
                        failed_batches += 1
                        
                        # Fallback към Route API за този batch
                        logger.debug(f"🛣️ Fallback към Route API за batch {i}-{end_i}, {j}-{end_j}")
                        self._fill_batch_with_routes(sources_batch, destinations_batch, 
                                                   full_distances, full_durations, i, j)
                        
                        pbar.update(1)
                        pbar.set_postfix({
                            'успешни': successful_batches,
                            'неуспешни': failed_batches,
                            'режим': 'Route fallback'
                        })
        
        # Финален отчет
        success_rate = (successful_batches / total_batch_requests) * 100 if total_batch_requests > 0 else 0
        logger.info(f"✅ Малки batch Table API матрица завършена:")
        logger.info(f"   🎯 Успешни batches: {successful_batches}/{total_batch_requests} ({success_rate:.1f}%)")
        logger.info(f"   ❌ Неуспешни batches: {failed_batches}")
        logger.info(f"   📊 Предимно реални OSRM данни от Table API")
        
        matrix = DistanceMatrix(
            distances=full_distances,
            durations=full_durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
        
        # Записваме в кеша
        if self.cache:
            self.cache.set(matrix)
            logger.info(f"💾 Записах малки batch Table API матрица в кеша за {len(locations)} локации")
        
        return matrix
    
    def _fill_batch_with_routes(self, sources_batch, destinations_batch, 
                               full_distances, full_durations, i, j):
        """Помощен метод за попълване на batch с Route API заявки"""
        for src_idx, src_loc in enumerate(sources_batch):
            for dest_idx, dest_loc in enumerate(destinations_batch):
                global_src = i + src_idx
                global_dest = j + dest_idx
                
                if global_src == global_dest:
                    full_distances[global_src][global_dest] = 0.0
                    full_durations[global_src][global_dest] = 0.0
                else:
                    try:
                        route_matrix = self._single_route_request(src_loc, dest_loc)
                        full_distances[global_src][global_dest] = route_matrix['distance']
                        full_durations[global_src][global_dest] = route_matrix['duration']
                    except:
                        approx_distance = self._haversine_distance(src_loc, dest_loc) * 1.3
                        full_distances[global_src][global_dest] = approx_distance
                        full_durations[global_src][global_dest] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
    
    def _get_matrix_via_routes(self, locations: List[Tuple[float, float]], base_url: str) -> DistanceMatrix:
        """Използва Route API за получаване на distance matrix (за локални сървъри)"""
        n = len(locations)
        
        # За големи матрици използваме приблизителни стойности
        if n > 100:  # Максимум 100 локации за Route API
            logger.warning(f"🚀 {n} локации е твърде много за Route API -> използвам приблизителни стойности")
            return self._create_approximate_matrix(locations)
        
        distances = [[0.0 for _ in range(n)] for _ in range(n)]
        durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        logger.info(f"Изчисляване на matrix чрез Route API за {n} локации ({n*n} заявки)")
        
        # Прогрес бар за route заявките
        total_requests = n * (n - 1)  # без диагонала
        with tqdm(total=total_requests, desc="🛣️ Route заявки", unit="req") as pbar:
            
            # За всяка двойка координати правим route заявка
            for i in range(n):
                for j in range(n):
                    if i == j:
                        distances[i][j] = 0.0
                        durations[i][j] = 0.0
                        continue
                    
                    try:
                        # Route заявка
                        route_url = self._build_route_url(locations[i], locations[j], base_url)
                        
                        response = self.session.get(route_url, timeout=10)  # по-кратък timeout
                        response.raise_for_status()
                        
                        data = response.json()
                        if data['code'] == 'Ok' and data['routes']:
                            route = data['routes'][0]
                            distances[i][j] = route['distance']  # в метри
                            durations[i][j] = route['duration']   # в секунди
                        else:
                            # Fallback към приблизителни стойности
                            approx_distance = self._haversine_distance(locations[i], locations[j]) * 1.3
                            distances[i][j] = approx_distance
                            durations[i][j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                            
                    except Exception as e:
                        logger.warning(f"Route заявка {i}->{j} неуспешна: {e}")
                        # Fallback към приблизителни стойности
                        approx_distance = self._haversine_distance(locations[i], locations[j]) * 1.3
                        distances[i][j] = approx_distance
                        durations[i][j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                    
                    # Обновяване на прогрес бара
                    pbar.update(1)
                    
                    # Много малка пауза
                    time.sleep(0.005)
        
        logger.info(f"✅ Matrix изчислена чрез Route API")
        
        matrix = DistanceMatrix(
            distances=distances,
            durations=durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
        
        # Записваме в кеша
        if self.cache:
            self.cache.set(matrix)
            logger.debug(f"💾 Записах Route API данни в кеша за {len(locations)} локации")
        
        return matrix

    def _single_route_request(self, src_loc: Tuple[float, float], dest_loc: Tuple[float, float]) -> dict:
        """Прави една Route API заявка за fallback"""
        route_url = self._build_route_url(src_loc, dest_loc)
        
        response = self.session.get(route_url, timeout=5)
        response.raise_for_status()
        
        data = response.json()
        if data['code'] == 'Ok' and data['routes']:
            route = data['routes'][0]
            return {
                'distance': route['distance'],
                'duration': route['duration']
            }
        else:
            raise Exception(f"Route API грешка: {data.get('code', 'Unknown')}")
    
    def _build_matrix_via_routes_only(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Изгражда пълна матрица използвайки САМО Route API заявки"""
        n = len(locations)
        distances = [[0.0 for _ in range(n)] for _ in range(n)]
        durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        total_requests = n * (n - 1)
        logger.info(f"🚀 Започвам изграждане на матрица: {n}×{n} = {total_requests} Route API заявки")
        
        with tqdm(total=total_requests, desc="🛣️ Route заявки", unit="req") as pbar:
            for i in range(n):
                for j in range(n):
                    if i == j:
                        distances[i][j] = 0.0
                        durations[i][j] = 0.0
                        continue
                    
                    try:
                        route_matrix = self._single_route_request(locations[i], locations[j])
                        distances[i][j] = route_matrix['distance']
                        durations[i][j] = route_matrix['duration']
                    except:
                        approx_distance = self._haversine_distance(locations[i], locations[j]) * 1.3
                        distances[i][j] = approx_distance
                        durations[i][j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                    
                    pbar.update(1)
        
        matrix = DistanceMatrix(
            distances=distances,
            durations=durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
        
        if self.cache:
            self.cache.set(matrix)
        
        return matrix
    
    def _create_approximate_matrix(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Създава приблизителна матрица с разстояния когато OSRM не работи"""
        n = len(locations)
        distances = [[0.0 for _ in range(n)] for _ in range(n)]
        durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        for i in range(n):
            for j in range(n):
                if i == j:
                    distances[i][j] = 0.0
                    durations[i][j] = 0.0
                else:
                    approx_distance = self._haversine_distance(locations[i], locations[j]) * 1.3
                    distances[i][j] = approx_distance
                    durations[i][j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
        
        return DistanceMatrix(
            distances=distances,
            durations=durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
    
    def _haversine_distance(self, coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
        """Изчислява разстояние по права линия в метри"""
        import math
        
        lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
        lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return 6371000 * c
    
    def _build_matrix_url(self, locations: List[Tuple[float, float]], base_url: Optional[str] = None) -> str:
        """Построява URL за OSRM matrix заявка"""
        if base_url is None:
            base_url = self.config.base_url
        
        base_url = base_url.rstrip('/')
        coords_str = ';'.join([f"{lon:.6f},{lat:.6f}" for lat, lon in locations])
        # Добавяме annotations=distance,duration, за да получим разстояния и времена
        url = f"{base_url}/table/v1/{self.config.profile}/{coords_str}?annotations=distance,duration"
        return url + self._approaches_query(len(locations))
    
    def _make_post_request(self, locations: List[Tuple[float, float]], base_url: str) -> requests.Response:
        """Прави POST заявка за големи координатни списъци"""
        clean_base_url = base_url.rstrip('/')
        url = f"{clean_base_url}/table/v1/{self.config.profile}"
        
        coordinates = [[lon, lat] for lat, lon in locations]
        # Добавяме annotations в post_data, за да получим разстояния и времена
        post_data = {
            "coordinates": coordinates,
            "annotations": ["distance", "duration"]
        }
        if self._curbside_enabled():
            post_data["approaches"] = ["curb"] * len(locations)
        
        response = self.session.post(
            url, 
            json=post_data,
            timeout=self.config.timeout_seconds,
            headers={'Content-Type': 'application/json'}
        )
        
        return response
    
    def close(self) -> None:
        """Затваря сесията"""
        self.session.close()

    def get_matrix_via_match_api(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Използва Match API за GPS точки с batch-ове от 30 локации"""
        n = len(locations)
        logger.info(f"🗺️ Започвам Match API обработка за {n} GPS локации")
        
        # Проверяваме кеша първо
        if self.cache:
            cached_matrix = self.cache.get(locations)
            if cached_matrix:
                logger.info(f"💾 Намерена кеширана Match API матрица за {n} локации")
                return cached_matrix
        
        # За малки datasets използваме директно Match API
        if n <= 30:
            return self._match_api_direct(locations)
        else:
            # За големи datasets използваме Match API batch-ове
            return self._build_matrix_via_match_batches(locations)
    
    def _match_api_direct(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Директен Match API за до 30 локации"""
        try:
            logger.info(f"🗺️ Match API заявка за {len(locations)} GPS точки")
            
            # Подготвяме координатите за Match API
            coordinates_str = ';'.join([f"{lon:.6f},{lat:.6f}" for lat, lon in locations])
            
            # Опитваме GET първо
            clean_base_url = self.config.base_url.rstrip('/')
            match_url = f"{clean_base_url}/match/v1/driving/{coordinates_str}?overview=full&geometries=geojson&steps=false&annotations=distance,duration"
            
            if len(match_url) <= 8000:  # Match API поддържа по-дълги URL-и
                response = self.session.get(match_url, timeout=30)
            else:
                # POST за много дълги URL-и
                response = self._make_match_post_request(locations)
            
            response.raise_for_status()
            data = response.json()
            
            if data['code'] != 'Ok':
                raise Exception(f"Match API грешка: {data.get('message', 'Неизвестна грешка')}")
            
            logger.info(f"✅ Успешен Match API за {len(locations)} GPS точки")
            
            # Обработваме Match API резултата
            return self._process_match_api_response(data, locations)
            
        except Exception as e:
            logger.warning(f"Match API неуспешен: {e}")
            
            # Fallback към Table API
            if self.config.fallback_to_public:
                logger.info(f"🌐 Fallback към Table API за {len(locations)} локации")
                return self._try_table_api_with_fallback(locations)
            else:
                raise e
    
    def _make_match_post_request(self, locations: List[Tuple[float, float]]):
        """POST заявка за Match API с много локации"""
        clean_base_url = self.config.base_url.rstrip('/')
        match_url = f"{clean_base_url}/match/v1/driving"
        
        # Подготвяме координатите за POST
        coordinates = [[lon, lat] for lat, lon in locations]
        
        payload = {
            "coordinates": coordinates,
            "overview": "full",
            "geometries": "geojson",
            "steps": False,
            "annotations": ["distance", "duration"]
        }
        
        response = self.session.post(match_url, json=payload, timeout=30)
        return response
    
    def _process_match_api_response(self, data, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Обработва Match API отговора и създава distance matrix"""
        n = len(locations)
        
        # Инициализираме матриците
        distances = [[0.0 for _ in range(n)] for _ in range(n)]
        durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        # Match API връща matched points и route geometry
        matchings = data.get('matchings', [])
        
        if matchings:
            # Вземаме първия matching (най-добрия)
            matching = matchings[0]
            
            # Получаваме annotations
            legs = matching.get('legs', [])
            
            # Изграждаме матрицата от leg-овете
            for i in range(len(legs)):
                leg = legs[i]
                leg_distance = leg.get('distance', 0)
                leg_duration = leg.get('duration', 0)
                
                # Попълваме матрицата
                distances[i][i+1] = leg_distance
                distances[i+1][i] = leg_distance  # Симетрично
                durations[i][i+1] = leg_duration
                durations[i+1][i] = leg_duration
            
            # Изчисляваме кумулативни разстояния за всички двойки
            for i in range(n):
                for j in range(i+2, n):
                    # Сумираме разстоянията между точките
                    total_distance = sum(distances[k][k+1] for k in range(i, j))
                    total_duration = sum(durations[k][k+1] for k in range(i, j))
                    
                    distances[i][j] = total_distance
                    distances[j][i] = total_distance  # Симетрично
                    durations[i][j] = total_duration
                    durations[j][i] = total_duration
        else:
            # Fallback към приблизителни стойности ако няма matching
            logger.warning("Match API не върна matchings, използвам приблизителни стойности")
            for i in range(n):
                for j in range(n):
                    if i != j:
                        approx_distance = self._haversine_distance(locations[i], locations[j]) * 1.3
                        distances[i][j] = approx_distance
                        durations[i][j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
        
        matrix = DistanceMatrix(
            distances=distances,
            durations=durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
        
        # Записваме в кеша
        if self.cache:
            self.cache.set(matrix)
            logger.info(f"💾 Записах Match API матрица в кеша за {n} локации")
        
        return matrix
    
    def _build_matrix_via_match_batches(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Изгражда матрица използвайки Match API batch-ове от 30 локации"""
        n = len(locations)
        batch_size = 30  # Намален размер за стабилност
        
        logger.info(f"🗺️ Започвам Match API batch обработка: {n} локации с batches от {batch_size}")
        
        # Инициализация на резултатни матрици
        full_distances = [[0.0 for _ in range(n)] for _ in range(n)]
        full_durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        successful_batches = 0
        failed_batches = 0
        
        # Изчисляваме брой batches
        num_batches = (n + batch_size - 1) // batch_size
        total_batch_requests = num_batches
        
        logger.info(f"📊 Ще направя {total_batch_requests} Match API batch заявки")
        
        # Прогрес бар за batch заявките
        with tqdm(total=total_batch_requests, desc="🗺️ Match API batches", unit="batch") as pbar:
            
            # Обработка на sequential batches (GPS траектории)
            for i in range(0, n, batch_size):
                end_i = min(i + batch_size, n)
                batch_locations = locations[i:end_i]
                
                try:
                    # Match API заявка за този batch
                    batch_matrix = self._match_api_direct(batch_locations)
                    
                    # Копиране на резултатите в пълната матрица
                    batch_size_actual = len(batch_locations)
                    for src_idx in range(batch_size_actual):
                        for dest_idx in range(batch_size_actual):
                            global_src = i + src_idx
                            global_dest = i + dest_idx
                            full_distances[global_src][global_dest] = batch_matrix.distances[src_idx][dest_idx]
                            full_durations[global_src][global_dest] = batch_matrix.durations[src_idx][dest_idx]
                    
                    successful_batches += 1
                    
                    # Обновяване на прогрес бара
                    pbar.update(1)
                    pbar.set_postfix({
                        'успешни': successful_batches,
                        'неуспешни': failed_batches,
                        'размер': f'{len(batch_locations)} GPS точки'
                    })
                    
                    # Малка пауза
                    time.sleep(0.1)
                    
                except Exception as e:
                    logger.warning(f"Match API batch {i}-{end_i} неуспешен: {e}")
                    failed_batches += 1
                    
                    # Fallback към Table API за този batch
                    logger.info(f"🛣️ Fallback към Table API за batch {i}-{end_i}")
                    try:
                        batch_matrix = self._try_table_api_with_fallback(batch_locations)
                        
                        # Копиране на резултатите
                        batch_size_actual = len(batch_locations)
                        for src_idx in range(batch_size_actual):
                            for dest_idx in range(batch_size_actual):
                                global_src = i + src_idx
                                global_dest = i + dest_idx
                                full_distances[global_src][global_dest] = batch_matrix.distances[src_idx][dest_idx]
                                full_durations[global_src][global_dest] = batch_matrix.durations[src_idx][dest_idx]
                    except:
                        # Последен fallback към приблизителни стойности
                        self._fill_batch_with_approximations(batch_locations, full_distances, full_durations, i)
                    
                    pbar.update(1)
                    pbar.set_postfix({
                        'успешни': successful_batches,
                        'неуспешни': failed_batches,
                        'режим': 'Fallback'
                    })
            
            # Попълваме междубbatch разстоянията
            logger.info(f"🔗 Попълвам междубbatch разстояния...")
            self._fill_inter_batch_distances(locations, full_distances, full_durations, batch_size)
        
        # Финален отчет
        success_rate = (successful_batches / total_batch_requests) * 100 if total_batch_requests > 0 else 0
        logger.info(f"✅ Match API batch матрица завършена:")
        logger.info(f"   🎯 Успешни batches: {successful_batches}/{total_batch_requests} ({success_rate:.1f}%)")
        logger.info(f"   ❌ Неуспешни batches: {failed_batches}")
        logger.info(f"   🗺️ Използвани GPS траектории за най-точни резултати")
        
        matrix = DistanceMatrix(
            distances=full_distances,
            durations=full_durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
        
        # Записваме в кеша
        if self.cache:
            self.cache.set(matrix)
            logger.info(f"💾 Записах Match API batch матрица в кеша за {len(locations)} локации")
        
        return matrix
    
    def _fill_batch_with_approximations(self, batch_locations, full_distances, full_durations, start_idx):
        """Попълва batch с приблизителни стойности като fallback"""
        for i, loc1 in enumerate(batch_locations):
            for j, loc2 in enumerate(batch_locations):
                global_i = start_idx + i
                global_j = start_idx + j
                
                if i == j:
                    full_distances[global_i][global_j] = 0.0
                    full_durations[global_i][global_j] = 0.0
                else:
                    approx_distance = self._haversine_distance(loc1, loc2) * 1.3
                    full_distances[global_i][global_j] = approx_distance
                    full_durations[global_i][global_j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
    
    def _fill_inter_batch_distances(self, locations, full_distances, full_durations, batch_size):
        """Попълва разстоянията между различни batch-ове"""
        n = len(locations)
        
        for i in range(0, n, batch_size):
            end_i = min(i + batch_size, n)
            
            for j in range(i + batch_size, n, batch_size):
                end_j = min(j + batch_size, n)
                
                # Обработваме всички двойки между два batch-а
                for src_idx in range(i, end_i):
                    for dest_idx in range(j, end_j):
                        src_loc = locations[src_idx]
                        dest_loc = locations[dest_idx]
                        
                        try:
                            # Опитваме Route API за междубbatch разстояния
                            forward_result = self._single_route_request(src_loc, dest_loc)
                            reverse_result = self._single_route_request(dest_loc, src_loc)
                            full_distances[src_idx][dest_idx] = forward_result['distance']
                            full_distances[dest_idx][src_idx] = reverse_result['distance']
                            full_durations[src_idx][dest_idx] = forward_result['duration']
                            full_durations[dest_idx][src_idx] = reverse_result['duration']
                        except:
                            # Fallback към приблизителни стойности
                            approx_distance = self._haversine_distance(src_loc, dest_loc) * 1.3
                            full_distances[src_idx][dest_idx] = approx_distance
                            full_distances[dest_idx][src_idx] = approx_distance
                            full_durations[src_idx][dest_idx] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                            full_durations[dest_idx][src_idx] = full_durations[src_idx][dest_idx]

    def _build_optimized_table_batches(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Оптимизиран batch Table API метод."""
        n = len(locations)
        # Inter-batch requests concatenate two batches, so each single batch
        # must be at most half of the OSRM Table API location limit.
        max_locations = int(getattr(self.config, "max_locations_for_osrm", 50) or 50)
        batch_size = max(2, min(25, max_locations // 2))
        
        logger.info(f"🚀 Започвам оптимизиран batch Table API: {n} локации с batch размер {batch_size}")
        
        # Инициализация на резултатни матрици
        full_distances = [[0.0 for _ in range(n)] for _ in range(n)]
        full_durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        successful_batches = 0
        failed_batches = 0
        
        # Изчисляваме брой batches - използваме стратегия на прозорци
        batch_requests = []
        for i in range(0, n, batch_size):
            end_i = min(i + batch_size, n)
            batch_requests.append((i, end_i))
        
        total_batch_requests = len(batch_requests)
        logger.info(f"📊 Ще направя {total_batch_requests} Table API заявки с до {batch_size} координати")
        
        # Прогрес бар за batch заявките
        with tqdm(total=total_batch_requests, desc="🚀 Оптимизиран Table API", unit="batch") as pbar:
            
            for batch_idx, (start_idx, end_idx) in enumerate(batch_requests):
                batch_locations = locations[start_idx:end_idx]
                batch_size_actual = len(batch_locations)
                
                try:
                    # Table API заявка за този batch
                    batch_matrix = self._try_table_api_with_fallback(batch_locations)
                    
                    # Копиране на резултатите в пълната матрица
                    for i in range(batch_size_actual):
                        for j in range(batch_size_actual):
                            global_i = start_idx + i
                            global_j = start_idx + j
                            full_distances[global_i][global_j] = batch_matrix.distances[i][j]
                            full_durations[global_i][global_j] = batch_matrix.durations[i][j]
                    
                    successful_batches += 1
                    
                    # Обновяване на прогрес бара
                    pbar.update(1)
                    pbar.set_postfix({
                        'успешни': successful_batches,
                        'неуспешни': failed_batches,
                        'координати': f'{batch_size_actual}'
                    })
                    
                    # Минимална пауза за да не претоварим сървъра
                    time.sleep(0.01)
                    
                except Exception as e:
                    logger.warning(f"Table API batch {start_idx}-{end_idx} неуспешен: {e}")
                    failed_batches += 1
                    
                    # Fallback към приблизителни стойности за този batch
                    logger.debug(f"📐 Използвам приблизителни стойности за batch {start_idx}-{end_idx}")
                    for i in range(batch_size_actual):
                        for j in range(batch_size_actual):
                            global_i = start_idx + i
                            global_j = start_idx + j
                            
                            if i == j:
                                full_distances[global_i][global_j] = 0.0
                                full_durations[global_i][global_j] = 0.0
                            else:
                                approx_distance = self._haversine_distance(batch_locations[i], batch_locations[j]) * 1.3
                                full_distances[global_i][global_j] = approx_distance
                                full_durations[global_i][global_j] = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                    
                    pbar.update(1)
                    pbar.set_postfix({
                        'успешни': successful_batches,
                        'неуспешни': failed_batches,
                        'режим': 'Приблизителни'
                    })
        
        # Попълваме междубbatch разстоянията с Route API
        logger.info(f"🔗 Попълвам междубbatch разстояния...")
        self._fill_inter_batch_connections(locations, full_distances, full_durations, batch_requests)
        
        # Финален отчет
        success_rate = (successful_batches / total_batch_requests) * 100 if total_batch_requests > 0 else 0
        logger.info(f"✅ Оптимизиран batch Table API завършен:")
        logger.info(f"   🎯 Успешни batches: {successful_batches}/{total_batch_requests} ({success_rate:.1f}%)")
        logger.info(f"   ❌ Неуспешни batches: {failed_batches}")
        logger.info(f"   🚀 Използвани Table API заявки с до {batch_size * 2} координати")
        
        matrix = DistanceMatrix(
            distances=full_distances,
            durations=full_durations,
            locations=locations,
            sources=list(range(len(locations))),
            destinations=list(range(len(locations)))
        )
        
        # Записваме в кеша
        if self.cache:
            self.cache.set(matrix)
            logger.info(f"💾 Записах оптимизирана batch матрица в кеша за {n} локации")
        
        return matrix
    
    def _fill_inter_batch_connections(self, locations, full_distances, full_durations, batch_requests):
        """Попълва разстоянията между различни batch-ове с оптимизирани Table API заявки"""
        n = len(locations)
        logger.info(f"🔗 Попълвам междубbatch връзки с Table API batch заявки...")
        
        # Обработваме всички двойки между различни batch-ове
        for i, (start_i, end_i) in enumerate(batch_requests):
            for j, (start_j, end_j) in enumerate(batch_requests):
                if i >= j:  # Обработваме само горната триъгълна матрица
                    continue
                
                # Събираме локациите от двата batch-а
                batch_i_locations = locations[start_i:end_i]
                batch_j_locations = locations[start_j:end_j]
                
                # Комбинираме локациите за Table API заявка
                inter_batch_locations = batch_i_locations + batch_j_locations
                
                try:
                    # Table API заявка за междубbatch връзки
                    logger.debug(f"🔗 Table API за batch {i}-{j}: {len(inter_batch_locations)} локации")
                    inter_batch_matrix = self._try_table_api_with_fallback(inter_batch_locations)
                    
                    # Попълваме междубbatch връзките в пълната матрица
                    batch_i_size = len(batch_i_locations)
                    batch_j_size = len(batch_j_locations)
                    
                    for src_idx_local, global_src in enumerate(range(start_i, end_i)):
                        for dest_idx_local, global_dest in enumerate(range(start_j, end_j)):
                            # Индекси в междубbatch матрицата
                            inter_src_idx = src_idx_local  # batch i локации са в началото
                            inter_dest_idx = batch_i_size + dest_idx_local  # batch j локации са след batch i
                            
                            reverse_src_idx = batch_i_size + dest_idx_local
                            reverse_dest_idx = src_idx_local
                            forward_distance = inter_batch_matrix.distances[inter_src_idx][inter_dest_idx]
                            reverse_distance = inter_batch_matrix.distances[reverse_src_idx][reverse_dest_idx]
                            forward_duration = inter_batch_matrix.durations[inter_src_idx][inter_dest_idx]
                            reverse_duration = inter_batch_matrix.durations[reverse_src_idx][reverse_dest_idx]
                            
                            # Store both Table API directions separately.
                            full_distances[global_src][global_dest] = forward_distance
                            full_distances[global_dest][global_src] = reverse_distance
                            full_durations[global_src][global_dest] = forward_duration
                            full_durations[global_dest][global_src] = reverse_duration
                    
                    logger.debug(f"✅ Table API успешна за batch {i}-{j}")
                    
                except Exception as e:
                    # Fallback към приблизителни стойности за цялото междубbatch пространство
                    logger.warning(f"🔗 Table API за batch {i}-{j} неуспешна: {e}, използвам приблизителни стойности")
                    
                    for src_idx in range(start_i, end_i):
                        for dest_idx in range(start_j, end_j):
                            src_loc = locations[src_idx]
                            dest_loc = locations[dest_idx]
                            
                            try:
                                forward_result = self._single_route_request(src_loc, dest_loc)
                                reverse_result = self._single_route_request(dest_loc, src_loc)
                                full_distances[src_idx][dest_idx] = forward_result['distance']
                                full_distances[dest_idx][src_idx] = reverse_result['distance']
                                full_durations[src_idx][dest_idx] = forward_result['duration']
                                full_durations[dest_idx][src_idx] = reverse_result['duration']
                                continue
                            except Exception:
                                approx_distance = self._haversine_distance(src_loc, dest_loc) * 1.3
                                approx_duration = (approx_distance / 1000) / self.config.average_speed_kmh * 3600
                            
                            # Last-resort approximation has no directionality.
                            full_distances[src_idx][dest_idx] = approx_distance
                            full_distances[dest_idx][src_idx] = approx_distance
                            full_durations[src_idx][dest_idx] = approx_duration
                            full_durations[dest_idx][src_idx] = approx_duration


def create_osrm_client() -> OSRMClient:
    """Удобна функция за създаване на OSRM клиент"""
    return OSRMClient()


def get_customer_distance_matrix(customers, depot_location: Tuple[float, float]) -> DistanceMatrix:
    """Получава матрица с разстояния за списък от клиенти"""
    client = create_osrm_client()
    
    # Подготовка на локациите (депо + клиенти)
    locations = [depot_location] + [customer.coordinates for customer in customers if customer.coordinates]
    
    try:
        return client.get_distance_matrix(locations)
    finally:
        client.close()


def get_distance_matrix_from_central_cache(locations: List[Tuple[float, float]]) -> Optional[DistanceMatrix]:
    """Получава матрица с разстояния директно от централния кеш (ПОДОБРЕНО с submatrix extraction)"""
    try:
        if not getattr(get_config().cache, "enable_cache", False):
            logger.info("Централният OSRM кеш е изключен от конфигурацията.")
            return None

        if getattr(get_config().routing, "enable_curbside_approach", False):
            logger.info("Централният OSRM кеш е пропуснат, защото curbside routing е включен.")
            return None

        # Създаваме кеш инстанция
        cache = OSRMCache(
            cache_file=os.path.join(get_config().cache.cache_dir, get_config().cache.osrm_cache_file),
            expiry_hours=24
        )
        
        # ПЪРВО опитваме директно търсене (най-бързо)
        cached_matrix = cache.get(locations)
        if cached_matrix:
            logger.info(f"💾 Намерена точна матрица в кеша за {len(locations)} локации")
            return cached_matrix
        
        # ВТОРО опитваме да извлечем от централната матрица
        logger.info(f"🔍 Търся в централната матрица за {len(locations)} локации...")
        central_matrix = cache.get_complete_central_matrix()
        
        if central_matrix:
            logger.info(f"📊 Намерена централна матрица с {len(central_matrix.locations)} локации")
            
            # Опитваме да извлечем подматрица
            submatrix = cache.extract_submatrix(central_matrix, locations)
            if submatrix:
                logger.info(f"✅ Извлечена подматрица {len(locations)}x{len(locations)} от централната матрица")
                return submatrix
            else:
                logger.warning(f"⚠️ Не мога да извлека подматрица - някои локации липсват в централната матрица")
        
        # Ако няма данни
        logger.info(f"❌ Няма данни в кеша за {len(locations)} локации")
        return None
        
    except Exception as e:
        logger.error(f"Грешка при четене от централния кеш: {e}")
        return None


def build_and_save_central_matrix(input_file: str = None) -> bool:
    """Създава и записва централна матрица от всички локации в input файла"""
    try:
        from input_handler import InputHandler
        
        # Зареждаме всички клиенти
        handler = InputHandler()
        input_data = handler.load_data(input_file)
        
        if not input_data.customers:
            logger.error("Няма валидни клиенти в input файла")
            return False
        
        # Създаваме пълен списък с локации (депо + всички клиенти)
        all_locations = [input_data.depot_location] + [c.coordinates for c in input_data.customers if c.coordinates]
        
        logger.info(f"🏗️ Създавам централна матрица за {len(all_locations)} локации...")
        logger.info(f"   📍 Депо: {input_data.depot_location}")
        logger.info(f"   👥 Клиенти: {len(input_data.customers)}")
        
        # Създаваме OSRM клиент
        client = OSRMClient()
        try:
            # Получаваме матрицата (това ще я запише автоматично в кеша)
            matrix = client.get_distance_matrix(all_locations)
            
            logger.info(f"✅ Централна матрица създадена успешно!")
            logger.info(f"   📊 Размер: {len(matrix.locations)}x{len(matrix.locations)}")
            logger.info(f"   💾 Записана в: cache/osrm_matrix_cache.json")
            
            return True
            
        finally:
            client.close()
        
    except Exception as e:
        logger.error(f"Грешка при създаване на централна матрица: {e}")
        return False


def get_central_matrix_info() -> dict:
    """Връща информация за централната матрица в кеша"""
    try:
        cache = OSRMCache(
            cache_file=os.path.join(get_config().cache.cache_dir, get_config().cache.osrm_cache_file),
            expiry_hours=24
        )
        
        central_matrix = cache.get_complete_central_matrix()
        
        if central_matrix:
            return {
                'exists': True,
                'locations_count': len(central_matrix.locations),
                'depot_location': central_matrix.locations[0] if central_matrix.locations else None,
                'cache_entries': len(cache.cache_data),
                'cache_file_size': os.path.getsize(cache.cache_file) if os.path.exists(cache.cache_file) else 0
            }
        else:
            return {
                'exists': False,
                'locations_count': 0,
                'depot_location': None,
                'cache_entries': len(cache.cache_data),
                'cache_file_size': os.path.getsize(cache.cache_file) if os.path.exists(cache.cache_file) else 0
            }
    
    except Exception as e:
        logger.error(f"Грешка при получаване на информация за централната матрица: {e}")
        return {'exists': False, 'error': str(e)}
