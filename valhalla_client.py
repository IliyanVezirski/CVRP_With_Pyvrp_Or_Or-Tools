"""
Valhalla клиент за изчисляване на разстояния и време за пътуване.
Поддържа time-dependent routing и truck routing.
"""

import requests
import json
import time
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
from tqdm import tqdm

from config import get_config, ValhallaConfig
from osrm_client import DistanceMatrix  # Използваме същия DistanceMatrix клас

logger = logging.getLogger(__name__)


class ValhallaClient:
    """Клиент за Valhalla API"""
    
    def __init__(self, config: Optional[ValhallaConfig] = None):
        self.config = config or get_config().valhalla
        self.routing_config = get_config().routing
        
        # HTTP session за по-бързи заявки
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'CVRP-Optimizer/1.0'
        })
        
        # HTTP connection pooling
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=self.config.retry_attempts,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=0.5
        )
        
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=retry_strategy
        )
        
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def _build_date_time_param(self) -> dict:
        """Създава date_time параметър за time-dependent routing"""
        if not self.routing_config.enable_time_dependent:
            return {}
        
        # Парсваме часа от конфигурацията
        departure_time = self.routing_config.departure_time
        today = datetime.now().strftime("%Y-%m-%d")
        
        return {
            "date_time": {
                "type": self.config.date_time_type,  # 1 = depart_at
                "value": f"{today}T{departure_time}"
            }
        }
    
    def _build_costing_options(self) -> dict:
        """Създава costing options за truck routing"""
        if self.config.costing != "truck":
            return {}
        
        return {
            "costing_options": {
                "truck": {
                    "height": self.config.truck_height,
                    "width": self.config.truck_width,
                    "weight": self.config.truck_weight
                }
            }
        }

    def _build_location(self, lat: float, lon: float) -> dict:
        location = {"lat": lat, "lon": lon}
        if getattr(self.routing_config, "enable_curbside_approach", False):
            location["preferred_side"] = getattr(self.routing_config, "valhalla_preferred_side", "same")
        return location
    
    def get_distance_matrix(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Получава матрица с разстояния и времена от Valhalla"""
        n_locations = len(locations)
        print(f"\n{'='*60}")
        print(f"🗺️  VALHALLA - Изчисляване на матрица")
        print(f"{'='*60}")
        print(f"📍 Брой локации: {n_locations}")
        print(f"📊 Размер на матрица: {n_locations}x{n_locations} = {n_locations*n_locations} клетки")
        
        if self.routing_config.enable_time_dependent:
            print(f"⏰ Time-dependent routing: ДА")
            print(f"🕐 Час на тръгване: {self.routing_config.departure_time}")
        else:
            print(f"⏰ Time-dependent routing: НЕ")
        
        print(f"🚗 Costing профил: {self.config.costing}")
        print(f"{'='*60}\n")
        
        logger.info(f"🗺️ Valhalla: Изчисляване на матрица за {n_locations} локации")
        
        if self.routing_config.enable_time_dependent:
            logger.info(f"⏰ Time-dependent routing: {self.routing_config.departure_time}")
        
        # За малки datasets (<=50) - използваме sources_to_targets API
        if n_locations <= 50:
            print(f"📡 Режим: Директна заявка (≤50 локации)")
            return self._get_matrix_direct(locations)
        else:
            # За по-големи - batch подход
            print(f"🧩 Режим: Batch заявки (>50 локации)")
            return self._get_matrix_batched(locations)
    
    def _get_matrix_direct(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Получава матрица директно чрез sources_to_targets API"""
        n = len(locations)
        print(f"📡 Изпращане на заявка към Valhalla...")
        print(f"   URL: {self.config.base_url}/sources_to_targets")
        
        # Подготовка на локациите
        valhalla_locations = [self._build_location(lat, lon) for lat, lon in locations]
        
        # Построяване на заявката
        request_body = {
            "sources": valhalla_locations,
            "targets": valhalla_locations,
            "costing": self.config.costing
        }
        
        # Добавяме time-dependent параметри
        request_body.update(self._build_date_time_param())
        request_body.update(self._build_costing_options())
        
        url = f"{self.config.base_url}/sources_to_targets"
        
        try:
            import time as time_module
            start_time = time_module.time()
            
            logger.info(f"📡 Valhalla API заявка: {n}x{n} матрица")
            response = self.session.post(
                url,
                json=request_body,
                timeout=self.config.timeout_seconds
            )
            response.raise_for_status()
            data = response.json()
            
            elapsed = time_module.time() - start_time
            print(f"✅ Отговор получен за {elapsed:.2f} секунди")
            
            # Парсване на отговора
            print(f"📊 Парсване на матрицата...")
            distances = [[0.0 for _ in range(n)] for _ in range(n)]
            durations = [[0.0 for _ in range(n)] for _ in range(n)]
            
            # Valhalla връща sources_to_targets като list of lists
            if "sources_to_targets" in data:
                total_cells = 0
                for i, row in enumerate(data["sources_to_targets"]):
                    for j, cell in enumerate(row):
                        if cell:
                            distances[i][j] = cell.get("distance", 0) * 1000  # km -> m
                            durations[i][j] = cell.get("time", 0)  # вече в секунди
                            total_cells += 1
                print(f"✅ Парсирани {total_cells} клетки от матрицата")
            
            # Статистика
            max_dist = max(max(row) for row in distances) / 1000
            max_time = max(max(row) for row in durations) / 60
            print(f"\n📈 Статистика:")
            print(f"   Макс. разстояние: {max_dist:.1f} км")
            print(f"   Макс. време: {max_time:.1f} мин")
            print(f"{'='*60}\n")
            
            logger.info(f"✅ Valhalla: Успешно получена {n}x{n} матрица")
            
            return DistanceMatrix(
                distances=distances,
                durations=durations,
                locations=locations,
                sources=list(range(n)),
                destinations=list(range(n))
            )
            
        except requests.exceptions.RequestException as e:
            print(f"❌ ГРЕШКА: {e}")
            logger.error(f"❌ Valhalla API грешка: {e}")
            raise
    
    def _get_matrix_batched(self, locations: List[Tuple[float, float]]) -> DistanceMatrix:
        """Получава матрица на части за големи datasets"""
        import time as time_module
        
        n = len(locations)
        batch_size = 50
        
        num_batches = (n + batch_size - 1) // batch_size
        total_requests = num_batches * num_batches
        
        print(f"\n🧩 Batch режим:")
        print(f"   Общо локации: {n}")
        print(f"   Batch размер: {batch_size}")
        print(f"   Брой batches: {num_batches}x{num_batches} = {total_requests} заявки")
        print(f"   URL: {self.config.base_url}/sources_to_targets\n")
        
        logger.info(f"🧩 Valhalla batch режим: {n} локации с batches от {batch_size}")
        
        distances = [[0.0 for _ in range(n)] for _ in range(n)]
        durations = [[0.0 for _ in range(n)] for _ in range(n)]
        
        start_time = time_module.time()
        successful_batches = 0
        failed_batches = 0
        
        with tqdm(total=total_requests, desc="🗺️ Valhalla batches", unit="batch") as pbar:
            for i in range(0, n, batch_size):
                end_i = min(i + batch_size, n)
                sources = locations[i:end_i]
                
                for j in range(0, n, batch_size):
                    end_j = min(j + batch_size, n)
                    targets = locations[j:end_j]
                    
                    try:
                        batch_matrix = self._get_submatrix(sources, targets)
                        
                        # Копиране в главната матрица
                        for si, src_idx in enumerate(range(i, end_i)):
                            for ti, tgt_idx in enumerate(range(j, end_j)):
                                distances[src_idx][tgt_idx] = batch_matrix['distances'][si][ti]
                                durations[src_idx][tgt_idx] = batch_matrix['durations'][si][ti]
                        
                        successful_batches += 1
                        
                    except Exception as e:
                        logger.warning(f"Batch {i}-{end_i} x {j}-{end_j} неуспешен: {e}")
                        failed_batches += 1
                        # Fallback към приблизителни стойности
                        for si, src_idx in enumerate(range(i, end_i)):
                            for ti, tgt_idx in enumerate(range(j, end_j)):
                                if src_idx != tgt_idx:
                                    approx = self._haversine_distance(
                                        locations[src_idx], locations[tgt_idx]
                                    ) * 1.3
                                    distances[src_idx][tgt_idx] = approx
                                    durations[src_idx][tgt_idx] = approx / 1000 / 40 * 3600
                    
                    pbar.update(1)
                    pbar.set_postfix({'✅': successful_batches, '❌': failed_batches})
                    time.sleep(0.01)
        
        elapsed = time_module.time() - start_time
        
        # Статистика
        max_dist = max(max(row) for row in distances) / 1000
        max_time = max(max(row) for row in durations) / 60
        
        print(f"\n{'='*60}")
        print(f"✅ Valhalla матрица завършена!")
        print(f"{'='*60}")
        print(f"   ⏱️  Време: {elapsed:.1f} секунди")
        print(f"   ✅ Успешни: {successful_batches}/{total_requests}")
        print(f"   ❌ Неуспешни: {failed_batches}/{total_requests}")
        print(f"   📏 Макс. разстояние: {max_dist:.1f} км")
        print(f"   ⏰ Макс. време: {max_time:.1f} мин")
        print(f"{'='*60}\n")
        
        logger.info(f"✅ Valhalla: Матрица {n}x{n} завършена")
        
        return DistanceMatrix(
            distances=distances,
            durations=durations,
            locations=locations,
            sources=list(range(n)),
            destinations=list(range(n))
        )
    
    def _get_submatrix(self, sources: List[Tuple[float, float]], 
                       targets: List[Tuple[float, float]]) -> dict:
        """Получава подматрица от Valhalla"""
        valhalla_sources = [self._build_location(lat, lon) for lat, lon in sources]
        valhalla_targets = [self._build_location(lat, lon) for lat, lon in targets]
        
        request_body = {
            "sources": valhalla_sources,
            "targets": valhalla_targets,
            "costing": self.config.costing
        }
        
        request_body.update(self._build_date_time_param())
        request_body.update(self._build_costing_options())
        
        url = f"{self.config.base_url}/sources_to_targets"
        
        response = self.session.post(
            url,
            json=request_body,
            timeout=self.config.timeout_seconds
        )
        response.raise_for_status()
        data = response.json()
        
        ns = len(sources)
        nt = len(targets)
        distances = [[0.0 for _ in range(nt)] for _ in range(ns)]
        durations = [[0.0 for _ in range(nt)] for _ in range(ns)]
        
        if "sources_to_targets" in data:
            for i, row in enumerate(data["sources_to_targets"]):
                for j, cell in enumerate(row):
                    if cell:
                        distances[i][j] = cell.get("distance", 0) * 1000
                        durations[i][j] = cell.get("time", 0)
        
        return {"distances": distances, "durations": durations}
    
    def get_route(self, origin: Tuple[float, float], 
                  destination: Tuple[float, float]) -> dict:
        """Получава маршрут между две точки"""
        request_body = {
            "locations": [
                self._build_location(origin[0], origin[1]),
                self._build_location(destination[0], destination[1])
            ],
            "costing": self.config.costing,
            "directions_options": {"units": "kilometers"}
        }
        
        request_body.update(self._build_date_time_param())
        request_body.update(self._build_costing_options())
        
        url = f"{self.config.base_url}/route"
        
        response = self.session.post(
            url,
            json=request_body,
            timeout=self.config.timeout_seconds
        )
        response.raise_for_status()
        data = response.json()
        
        if "trip" in data and "legs" in data["trip"]:
            leg = data["trip"]["legs"][0]["summary"]
            return {
                "distance": leg.get("length", 0) * 1000,  # km -> m
                "duration": leg.get("time", 0)  # seconds
            }
        
        return {"distance": 0, "duration": 0}
    
    def _haversine_distance(self, coord1: Tuple[float, float], 
                           coord2: Tuple[float, float]) -> float:
        """Изчислява разстояние по Haversine формулата (в метри)"""
        import math
        
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        R = 6371000  # Радиус на Земята в метри
        
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_phi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def check_server_status(self) -> bool:
        """Проверява дали Valhalla сървърът е достъпен"""
        try:
            # Правим проста заявка за статус
            url = f"{self.config.base_url}/status"
            logger.info(f"🔍 Проверка на Valhalla сървър: {url}")
            response = self.session.get(url, timeout=10)
            logger.info(f"✅ Valhalla отговор: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                logger.info(f"📊 Valhalla версия: {data.get('version', 'unknown')}")
                return True
            return False
        except Exception as e:
            logger.warning(f"❌ Valhalla не е достъпен: {e}")
            return False
    
    def close(self):
        """Затваря HTTP сесията"""
        self.session.close()


def create_valhalla_client() -> ValhallaClient:
    """Factory функция за създаване на Valhalla клиент"""
    return ValhallaClient()
