"""
Главен файл за CVRP програма - Оркестратор на процеси
Координира всички модули за решаване на Vehicle Routing Problem,
включително паралелна обработка с различни стратегии.
"""

import logging
import sys
import time
import os
import io
import copy
from typing import Optional, List, Dict, Any, Tuple
from multiprocessing import Pool, cpu_count
from dataclasses import asdict

# Импортираме всички необходими модули
from ortools.constraint_solver import routing_enums_pb2
from config import get_config, MainConfig, CVRPConfig, LocationConfig, RoutingEngine
from input_handler import InputHandler, InputData
from warehouse_manager import WarehouseManager, WarehouseAllocation
from cvrp_solver import CVRPSolver, CVRPSolution
from pyvrp_solver import solve_cvrp_pyvrp
from output_handler import OutputHandler
from osrm_client import OSRMClient, DistanceMatrix, get_distance_matrix_from_central_cache


def setup_logging():
    """Настройва основното логиране за главния процес."""
    config = get_config()
    log_config = config.logging
    
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    formatter = logging.Formatter(log_config.log_format)
    
    logger = logging.getLogger()
    try:
        logger.setLevel(getattr(logging, log_config.log_level.upper()))
    except AttributeError:
        logger.setLevel(logging.INFO)
    
    if log_config.enable_console_logging:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    if log_config.enable_file_logging:
        os.makedirs(os.path.dirname(log_config.log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_config.log_file, 'a', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def prepare_data(
    input_file: Optional[str],
    input_data_override: Optional[InputData] = None,
) -> Tuple[Optional[InputData], Optional[WarehouseAllocation]]:
    """
    Стъпка 1: Подготвя всички входни данни.
    """
    logger = logging.getLogger(__name__)
    logger.info("="*60)
    logger.info("СТЪПКА 1: ПОДГОТОВКА НА ДАННИ")
    logger.info("="*60)
    
    warehouse_manager = WarehouseManager()

    try:
        if input_data_override is not None:
            input_data = input_data_override
            logger.info("Използвам клиентски данни, подадени директно към процеса.")
        else:
            input_handler = InputHandler()
            input_data = input_handler.load_data(input_file)

        if not input_data or not input_data.customers:
            logger.error("Не са намерени валидни клиенти във входния файл.")
            return None, None
        
        logger.info(f"Заредени {len(input_data.customers)} клиенти с общ обем {input_data.total_volume:.2f} ст.")
        
        warehouse_allocation = warehouse_manager.allocate_customers(input_data)
        warehouse_allocation = warehouse_manager.optimize_allocation(warehouse_allocation)
        
        logger.info(f"Разпределение: {len(warehouse_allocation.vehicle_customers)} за бусове, "
                    f"{len(warehouse_allocation.warehouse_customers)} за склад.")
        logger.info(f"Използване на капацитета: {warehouse_allocation.capacity_utilization*100:.1f}%")

        return input_data, warehouse_allocation
    except Exception as e:
        logger.error(f"Фатална грешка при подготовка на данните: {e}", exc_info=True)
        return None, None


def get_distance_matrix(
    allocation: WarehouseAllocation, 
    location_config: LocationConfig
) -> Optional[DistanceMatrix]:
    """
    Изчислява или зарежда от кеша матрицата с разстояния САМО ВЕДНЪЖ.
    """
    logger = logging.getLogger(__name__)
    config = get_config()
    
    logger.info("="*60)
    logger.info("СТЪПКА 1.5: ИЗЧИСЛЯВАНЕ НА МАТРИЦА С РАЗСТОЯНИЯ")
    logger.info("="*60)

    customers = allocation.vehicle_customers
    if not customers:
        logger.warning("Няма клиенти за solver-а, пропускам изчисляването на матрица.")
        return None
        
    enabled_vehicles = config.vehicles or []
    unique_depots = {location_config.depot_location}
    for vehicle_config in enabled_vehicles:
        if vehicle_config.enabled and vehicle_config.start_location:
            unique_depots.add(vehicle_config.start_location)
            
    sorted_depots = sorted(list(unique_depots), key=lambda x: (x[0], x[1]))

    all_locations = sorted_depots + [c.coordinates for c in customers if c.coordinates]
    
    logger.info(f"Общо локации за матрица: {len(all_locations)} ({len(sorted_depots)} депа, {len(customers)} клиента)")
    
    # Избор на routing engine
    routing_engine = config.routing.engine
    logger.info(f"🗺️ Routing engine: {routing_engine.value.upper()}")
    
    distance_matrix = None
    use_osrm_fallback = False
    
    # Използваме .value сравнение за избягване на проблеми с enum instance-и
    is_valhalla = (routing_engine.value == RoutingEngine.VALHALLA.value)
    logger.info(f"🔍 Сравнение: {routing_engine.value} == {RoutingEngine.VALHALLA.value} -> {is_valhalla}")
    
    if is_valhalla:
        # Използваме Valhalla
        logger.info(f"✅ Влизам в Valhalla блок")
        logger.info(f"⏰ Time-dependent: {config.routing.enable_time_dependent}")
        if config.routing.enable_time_dependent:
            logger.info(f"🕐 Час на тръгване: {config.routing.departure_time}")
        
        from valhalla_client import ValhallaClient
        valhalla_client = ValhallaClient()
        
        # Проверка дали сървърът е достъпен
        if not valhalla_client.check_server_status():
            logger.warning("⚠️ Valhalla сървърът не е достъпен! Fallback към OSRM...")
            use_osrm_fallback = True
        else:
            try:
                distance_matrix = valhalla_client.get_distance_matrix(all_locations)
            except Exception as e:
                logger.error(f"❌ Valhalla грешка: {e}")
                logger.info("Fallback към OSRM...")
                use_osrm_fallback = True
            finally:
                valhalla_client.close()
    else:
        use_osrm_fallback = True
    
    # OSRM (default или fallback)
    if use_osrm_fallback:
        logger.info("🗺️ Използвам OSRM...")
        distance_matrix = get_distance_matrix_from_central_cache(all_locations)
        
        if distance_matrix is None:
            logger.info("Няма данни в кеша - правя нова OSRM заявка...")
            osrm_client = OSRMClient()
            try:
                distance_matrix = osrm_client.get_distance_matrix(all_locations)
            finally:
                osrm_client.close()
        else:
            logger.info("Успешно заредена матрица от централния кеш.")
        
    return distance_matrix


def generate_solver_configs(base_cvrp_config: CVRPConfig, num_workers: int) -> List[CVRPConfig]:
    """
    Генерира списък с различни конфигурации за паралелно тестване.
    Взима стратегиите последователно от списъка.
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Генерирам {num_workers} варианта на конфигурации за паралелно решаване...")
    
    configs = []
    
    first_solution_strategies = base_cvrp_config.parallel_first_solution_strategies
    local_search_metaheuristics = base_cvrp_config.parallel_local_search_metaheuristics

    # Взимаме стратегиите последователно от списъка
    for i in range(num_workers):
        # Избираме стратегия от списъка (циклично ако няма достатъчно)
        strategy_index = i % len(first_solution_strategies)
        metaheuristic_index = i % len(local_search_metaheuristics)
        
        strategy = first_solution_strategies[strategy_index]
        metaheuristic = local_search_metaheuristics[metaheuristic_index]
        
        new_config = copy.deepcopy(base_cvrp_config)
        new_config.first_solution_strategy = strategy
        new_config.local_search_metaheuristic = metaheuristic
        
        configs.append(new_config)

    logger.info(f"Създадени {len(configs)} конфигурации за тестване.")
    logger.info(f"Използвани стратегии: {[c.first_solution_strategy for c in configs]}")
    logger.info(f"Използвани метаевристики: {[c.local_search_metaheuristic for c in configs]}")
    
    return configs


def solve_cvrp_worker(worker_args: Tuple[WarehouseAllocation, Dict, Dict, DistanceMatrix, int]) -> Optional[CVRPSolution]:
    """
    "Работникът" - функцията, която се изпълнява паралелно.
    """
    warehouse_allocation, cvrp_config_dict, location_config_dict, distance_matrix, worker_id = worker_args
    cvrp_config = CVRPConfig(**cvrp_config_dict)
    location_config = LocationConfig(**location_config_dict)

    # Добавяме лог за дебъгване
    print(f"[Работник {worker_id}]: solver_type = {cvrp_config.solver_type}, use_simple_solver = {cvrp_config.use_simple_solver}")

    print(f"[Работник {worker_id}]: СТАРТ. Стратегия: {cvrp_config.first_solution_strategy}, "
          f"Метаевристика: {cvrp_config.local_search_metaheuristic}")

    # Избираме подходящия солвър
    if cvrp_config.solver_type == "pyvrp":
        solution = solve_cvrp_pyvrp(
            allocation=warehouse_allocation,
            depot_location=location_config.depot_location,
            distance_matrix=distance_matrix,
            config=cvrp_config,
            location_config=location_config
        )
    else:  # or_tools
        solver = CVRPSolver(cvrp_config, location_config)
        solution = solver.solve(warehouse_allocation, location_config.depot_location, distance_matrix)

    if solution and solution.routes:
        # Изчисляваме общия обем за това решение
        total_volume = sum(r.total_volume for r in solution.routes)
        print(f"[Работник {worker_id}]: ЗАВЪРШЕН. Обслужен обем: {total_volume:.2f}, "
              f"Маршрути: {len(solution.routes)}, Пропуснати: {len(solution.dropped_customers)}")
        return solution

    print(f"[Работник {worker_id}]: ЗАВЪРШЕН. Не е намерено валидно решение.")
    return None


def process_results(
    solution: CVRPSolution,
    input_data: InputData,
    warehouse_allocation: WarehouseAllocation,
    execution_time: float,
    sorted_depots: List[Tuple[float, float]]
):
    """
    Стъпка 3: Обработва финалното (най-доброто) решение.
    """
    logger = logging.getLogger(__name__)
    logger.info("="*60)
    logger.info("СТЪПКА 3: ОБРАБОТКА НА РЕЗУЛТАТИТЕ")
    logger.info("="*60)
    
    output_handler = OutputHandler()
    
    logger.info("Генериране на изходни файлове...")
    # Предаваме входното депо за съвместимост (ще се използват индивидуалните депа от маршрутите)
    output_files = output_handler.generate_all_outputs(
                solution, warehouse_allocation, input_data.depot_location
            )
            
    _print_summary(input_data, warehouse_allocation, solution, output_files, execution_time)
    
    logger.info("="*60)
    logger.info("CVRP ОПТИМИЗАЦИЯ ЗАВЪРШЕНА УСПЕШНО")
    logger.info("="*60)
    return output_files


def _print_summary(input_data, warehouse_allocation, solution, output_files, execution_time):
    """Отпечатва финално резюме на изпълнението."""
    logger = logging.getLogger(__name__)
    logger.info("\n" + "="*50)
    logger.info("РЕЗЮМЕ НА ОПТИМИЗАЦИЯТА")
    logger.info("="*50)
    
    logger.info(f"Време за изпълнение: {execution_time:.2f} секунди")
    
    logger.info("\nCVRP РЕШЕНИЕ:")
    logger.info(f"  Използвани превозни средства: {solution.total_vehicles_used}")
    logger.info(f"  Общо разстояние: {solution.total_distance_km:.2f} км")
    logger.info(f"  Общо време: {solution.total_time_minutes:.1f} минути")
    logger.info(f"  Fitness оценка: {solution.fitness_score:.2f}")
    logger.info(f"  Пропуснати клиенти: {len(solution.dropped_customers)}")
    
    # Детайли по маршрути
    if solution.routes:
        logger.info("\nДЕТАЙЛИ ПО МАРШРУТИ:")
        for i, route in enumerate(solution.routes):
            vehicle_name = route.vehicle_type.value.replace('_', ' ').title()
            logger.info(f"  Маршрут {i+1} ({vehicle_name}):")
            logger.info(f"    Клиенти: {len(route.customers)}, Обем: {route.total_volume:.2f} ст., "
                        f"Разстояние: {route.total_distance_km:.2f} км, Време: {route.total_time_minutes:.1f} мин")
    
    # Изходни файлове
    logger.info("\nГЕНЕРИРАНИ ФАЙЛОВЕ:")
    for file_type, file_path in output_files.items():
        file_type_name = file_type.replace('_', ' ').title()
        logger.info(f"  {file_type_name}: {file_path}")
    
    logger.info("="*50)


def _solution_to_api_response(
    solution: CVRPSolution,
    input_data: InputData,
    warehouse_allocation: WarehouseAllocation,
    output_files: Dict[str, str],
    execution_time: float,
    set_data_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Създава JSON-съвместимо резюме за API отговора."""
    response = {
        "status": "ok",
        "execution_time_seconds": round(execution_time, 2),
        "customers_total": len(input_data.customers),
        "customers_for_routes": len(warehouse_allocation.vehicle_customers),
        "customers_for_warehouse": len(warehouse_allocation.warehouse_customers),
        "routes_count": len(solution.routes),
        "dropped_customers_count": len(solution.dropped_customers),
        "total_vehicles_used": solution.total_vehicles_used,
        "total_distance_km": round(solution.total_distance_km, 2),
        "total_time_minutes": round(solution.total_time_minutes, 1),
        "fitness_score": round(solution.fitness_score, 2),
        "output_files": output_files,
        "routes": [
            {
                "vehicle_type": route.vehicle_type.value,
                "customers_count": len(route.customers),
                "total_volume": route.total_volume,
                "total_distance_km": round(route.total_distance_km, 2),
                "total_time_minutes": round(route.total_time_minutes, 1),
                "customers": [
                    {
                        "id": customer.id,
                        "name": customer.name,
                        "volume": customer.volume,
                        "document": customer.document,
                        "plas_doc": getattr(customer, "plas_doc", ""),
                        "source_id_skld": getattr(customer, "source_id_skld", ""),
                    }
                    for customer in route.customers
                ],
            }
            for route in solution.routes
        ],
        "dropped_customers": [
            {
                "id": customer.id,
                "name": customer.name,
                "volume": customer.volume,
                "document": customer.document,
                "plas_doc": getattr(customer, "plas_doc", ""),
                "source_id_skld": getattr(customer, "source_id_skld", ""),
            }
            for customer in solution.dropped_customers
        ],
    }
    if set_data_result is not None:
        response["set_data"] = set_data_result
    return response


def run_optimization(
    input_file: Optional[str] = None,
    input_data_override: Optional[InputData] = None,
) -> Dict[str, Any]:
    """Изпълнява цялата CVRP оптимизация и връща JSON-съвместимо резюме."""
    start_time = time.time()
    
    setup_logging()
    logger = logging.getLogger(__name__)
    
    config = get_config()

    input_data, warehouse_allocation = prepare_data(input_file, input_data_override)
    if not input_data or not warehouse_allocation:
        raise RuntimeError("Не са намерени валидни клиенти във входните данни.")

    # --- Стъпка 1.5: Изчисляване на матрица с разстояния ---
    distance_matrix = get_distance_matrix(warehouse_allocation, config.locations)
    if not distance_matrix:
        logger.error("Не може да се изчисли матрица с разстояния. Прекратявам работа.")
        raise RuntimeError("Не може да се изчисли матрица с разстояния.")

    logger.info("="*60)
    logger.info("СТЪПКА 2: РЕШАВАНЕ НА CVRP ПРОБЛЕМА")
    logger.info("="*60)

    best_solution = None

    cpu_cores = os.cpu_count() or 1
    if config.cvrp.enable_parallel_solving and cpu_cores > 1:
        if config.cvrp.num_workers == -1:
            num_workers = max(1, cpu_cores - 1)
        else:
            num_workers = config.cvrp.num_workers
        
        logger.info(f"🚀 Старирам паралелна обработка с {num_workers} работника...")
        
        solver_configs = generate_solver_configs(config.cvrp, num_workers)
        
        # Подготвяме аргументи за всеки работник
        worker_args = []
        for i, cvrp_config in enumerate(solver_configs):
            # Подаваме само необходимите части от конфигурацията, а не целия обект
            # Превръщаме ги в речници, за да избегнем pickling грешки.
            args = (
                warehouse_allocation,
                asdict(cvrp_config),
                asdict(config.locations),
                distance_matrix, # Подаваме готовата матрица
                i + 1
            )
            worker_args.append(args)

        with Pool(processes=num_workers) as pool:
            results = pool.map(solve_cvrp_worker, worker_args)
        
        valid_solutions = [sol for sol in results if sol is not None]
        
        if valid_solutions:
            # ИЗБИРАМЕ ПОБЕДИТЕЛЯ ПО НАЙ-ДОБЪР ФИТНЕС СКОР (НАЙ-МАЛКО РАЗСТОЯНИЕ)
            for sol in valid_solutions:
                 sol.total_served_volume = sum(r.total_volume for r in sol.routes)

            best_solution = min(valid_solutions, key=lambda s: s.fitness_score)
            
            logger.info(f"🏆 Избрано е най-доброто решение по ФИТНЕС СКОР от {len(valid_solutions)} намерени, "
                        f"с fitness score: {best_solution.fitness_score:.2f} (разстояние: {best_solution.total_distance_km:.1f}км)")
        else:
            logger.error("Всички паралелни работници се провалиха. Не е намерено решение.")

    else:
        # ЕДИНИЧЕН РЕЖИМ (опростен)
        logger.info("⚙️ Стартирам в единичен режим.")
        best_solution = solve_cvrp_worker((
            warehouse_allocation, 
            asdict(config.cvrp), 
            asdict(config.locations),
            distance_matrix,
            1
        ))

    if best_solution:
        execution_time = time.time() - start_time
        # Получаваме депата за передаване към process_results
        enabled_vehicles = get_config().vehicles or []
        unique_depots = {config.locations.depot_location}
        for vehicle_config in enabled_vehicles:
            if vehicle_config.enabled and vehicle_config.start_location:
                unique_depots.add(vehicle_config.start_location)
        
        # Гарантираме, че главното депо е винаги първо в списъка
        sorted_depots = [config.locations.depot_location]  # Главното депо винаги първо
        other_depots = sorted([d for d in unique_depots if d != config.locations.depot_location], key=lambda x: (x[0], x[1]))
        sorted_depots.extend(other_depots)
        
        output_files = process_results(best_solution, input_data, warehouse_allocation, execution_time, sorted_depots)
        set_data_result = None
        try:
            from setdata_client import upload_solution_set_data
            set_data_result = upload_solution_set_data(best_solution, config, warehouse_allocation)
        except Exception as exc:
            logger.error(f"setData изпращането завърши с грешка: {exc}", exc_info=True)
            set_data_result = {"enabled": True, "attempted": 0, "succeeded": 0, "failed": 1, "errors": [{"error": str(exc)}]}
        print("\n[OK] CVRP оптимизация завършена успешно!")
        return _solution_to_api_response(
            best_solution,
            input_data,
            warehouse_allocation,
            output_files,
            execution_time,
            set_data_result,
        )
    else:
        logger.error("[ERROR] Не успях да намеря решение на проблема.")
        print("\n[ERROR] CVRP оптимизация завършена с грешки!")
        raise RuntimeError("Не успях да намеря решение на проблема.")


def main():
    """Главна функция - оркестратор."""
    input_file = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        run_optimization(input_file=input_file)
    except Exception as exc:
        logging.getLogger(__name__).error(f"Критична грешка: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    # Fix encoding for Windows console (support UTF-8 output)
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    try:
        main()
    except Exception as e:
        logging.getLogger(__name__).error(f"Критична грешка: {e}", exc_info=True)
        sys.exit(1)
