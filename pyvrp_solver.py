"""

PYVRP CVRP Solver - Алтернативен решател използвайки PyVRP библиотека

Този модул предоставя адаптер за PyVRP, който поддържа същият интерфейс като ORToolsSolver,
но използва PyVRP для решаване на Vehicle Routing Problem с Capacities (CVRP).

PyVRP е специализирана библиотека за VRP оптимизация, базирана на Iterated Local Search.

"""

from __future__ import annotations

import logging
import math
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

try:
    import pyvrp
    from pyvrp import Model, solve, SolveParams, constants
    from pyvrp.solve import IteratedLocalSearchParams, PenaltyParams
    from pyvrp.search import (
        NeighbourhoodParams,
        PerturbationParams,
        Exchange10,
        Exchange11,
        Exchange20,
        Exchange21,
        Exchange22,
        Exchange30,
        Exchange31,
        Exchange32,
        Exchange33,
        RelocateWithDepot,
        SwapTails,
    )
    # Route-level operators exist in the stable 0.13 API, but were folded into
    # the unified operator API on the experimental 0.14 line.
    SwapRoutes = getattr(pyvrp.search, "SwapRoutes", None)
    SwapStar = getattr(pyvrp.search, "SwapStar", None)
    PYVRP_DEFAULT_OPERATORS = list(getattr(pyvrp.search, "OPERATORS", []))
    from pyvrp.stop import MaxRuntime
    try:
        PYVRP_VERSION = package_version("pyvrp")
    except PackageNotFoundError:
        PYVRP_VERSION = "unknown"
    PYVRP_LOCATION_API = hasattr(Model, "add_location")
    PYVRP_AVAILABLE = True
except (ImportError, AttributeError):
    PYVRP_VERSION = "unavailable"
    PYVRP_LOCATION_API = False
    PYVRP_DEFAULT_OPERATORS = []
    PYVRP_AVAILABLE = False
    logging.warning("PyVRP ne e instaliran. Shte se izpolzva OR-Tools.")

from config import (
    CVRPConfig,
    VehicleConfig,
    LocationConfig,
    calculate_customer_drop_penalties,
    is_location_in_center_zone,
    center_zone_cost_adjustment,
    center_zone_profile_signature,
    build_ordered_depots,
    get_traffic_multiplier,
    get_traffic_zones,
)
from input_handler import (
    Customer,
    choose_time_window_for_arrival,
    customer_time_windows_minutes,
    format_time_windows_minutes,
    is_mandatory_customer,
    time_windows_to_seconds,
)
from osrm_client import DistanceMatrix
from warehouse_manager import WarehouseAllocation
from cvrp_solver import (
    ORTOOLS_AVAILABLE,
    ORToolsSolver,
    Route,
    CVRPSolution,
    calculate_distance_km,
)


logger = logging.getLogger(__name__)

# Reporting recomputes OSRM travel and traffic-adjusted durations with their
# fractional seconds, while PyVRP operates on integer durations.  Keep the
# final hard-limit audit consistent with the one-minute rounding allowance
# already used by the OR-Tools output path.
WORK_TIME_AUDIT_TOLERANCE_MINUTES = 1.0

# Full-workday time is expressed directly in seconds. Distance must not leak
# into the objective when the configured metric is ``time``.
_FULL_WORKDAY_TIME_COST_WEIGHT = 1


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
        self._pyvrp_client_to_customer_idx: List[int] = []
        self._pyvrp_location_matrix_indices: List[int] = []
        self._pyvrp_depot_coords: List[Tuple[float, float]] = []

    @staticmethod
    def _add_model_depot(model: Model, x: float, y: float, **kwargs):
        """Add a depot through either the 0.13 or 0.14 modelling API.

        PyVRP 0.14 separates physical locations from depot/client activities.
        The returned pair is ``(depot, edge_location)``: vehicle types always
        reference the first item, while distance edges always reference the
        second.  In 0.13 both roles are represented by the depot itself.
        """
        if PYVRP_LOCATION_API:
            name = str(kwargs.get("name", "") or "")
            location = model.add_location(x=x, y=y, name=f"{name}_location")
            depot = model.add_depot(location=location, **kwargs)
            return depot, location

        depot = model.add_depot(x=x, y=y, **kwargs)
        return depot, depot

    @staticmethod
    def _add_model_client(model: Model, x: float, y: float, **kwargs):
        """Add a client and return ``(client, edge_location)`` for both APIs."""
        if PYVRP_LOCATION_API:
            name = str(kwargs.get("name", "") or "")
            location = model.add_location(x=x, y=y, name=f"{name}_location")
            client = model.add_client(location=location, **kwargs)
            return client, location

        client = model.add_client(x=x, y=y, **kwargs)
        return client, client

    def _multiple_trips_enabled(self) -> bool:
        return bool(getattr(self.config, "enable_multiple_trips", False))

    def _annotate_solution(
        self,
        solution: CVRPSolution,
        *,
        solver_used: Optional[str] = None,
        fallback_used: bool = False,
        fallback_reason: str = "",
    ) -> CVRPSolution:
        requested = str(getattr(self.config, "solver_type", "pyvrp") or "pyvrp")
        used = solver_used or requested
        solution.solver_requested = requested
        solution.solver_used = used
        solution.solver_backend = used
        solution.solver_version = PYVRP_VERSION if used.startswith("pyvrp") else ""
        solution.solver_fallback_used = bool(fallback_used)
        solution.solver_fallback_reason = str(fallback_reason or "")
        return solution

    def _exact_ortools_fallback_reason(self) -> Optional[str]:
        """Return why this model needs an exact OR-Tools resource dimension.

        PyVRP 0.13 reloads every load dimension between trips, so it cannot
        express a customer-cardinality limit that remains cumulative across a
        physical vehicle's whole day. In time-objective mode the auxiliary
        distance field is reserved for policy penalties, leaving no separate
        native field for a real-kilometre cap. OR-Tools models both cumulative
        daily limits exactly.
        """
        enabled = [vehicle for vehicle in self.vehicle_configs if vehicle.enabled and vehicle.count > 0]
        for vehicle in enabled:
            daily_limit = getattr(vehicle, "max_customers_per_day", None)
            if daily_limit is None:
                daily_limit = getattr(vehicle, "max_customers_per_route", None)
            try:
                if daily_limit is not None and 0 < int(daily_limit) < len(self.customers):
                    # OR-Tools has a dedicated hard dimension for this. PyVRP
                    # can represent it as a load dimension for one trip, but
                    # its penalty search can still end on an infeasible plan
                    # when optional clients carry large prizes. Use the exact
                    # implementation whenever this hard daily limit matters.
                    return "hard max_customers_per_day"
            except (TypeError, ValueError, OverflowError):
                continue

        if self._objective_metric() == "time":
            for vehicle in enabled:
                try:
                    if float(getattr(vehicle, "max_distance_km", 0) or 0) > 0:
                        return "real max_distance_km with time objective"
                except (TypeError, ValueError, OverflowError):
                    continue
        return None

    def _dynamic_max_reloads(self, vehicle_config: VehicleConfig) -> int:
        """Finite solver-only upper bound; the user never configures trip count."""
        if not self._multiple_trips_enabled() or not self.customers:
            return 0

        # A useful trip serves at least one customer, so more than N - 1
        # reloads can never improve a solution with N customers.
        bounds = [max(0, len(self.customers) - 1)]

        daily_limit = getattr(vehicle_config, "max_customers_per_day", None)
        if daily_limit is None:
            daily_limit = getattr(vehicle_config, "max_customers_per_route", None)
        if daily_limit is not None and int(daily_limit) > 0:
            bounds.append(max(0, int(daily_limit) - 1))

        shift_minutes = max(0, int(round(float(vehicle_config.max_time_hours or 0) * 60)))
        default_service_minutes = max(
            0,
            float(getattr(vehicle_config, "service_time_minutes", 0) or 0),
        )
        effective_service_minutes = []
        for customer in self.customers:
            override = getattr(customer, "service_time_minutes", None)
            if override is None or (isinstance(override, str) and not override.strip()):
                effective_service_minutes.append(default_service_minutes)
            else:
                try:
                    effective_service_minutes.append(max(0.0, float(override)))
                except (TypeError, ValueError, OverflowError):
                    effective_service_minutes.append(default_service_minutes)
        # Use the smallest effective service time to keep this a conservative
        # upper bound: it may allow extra reload candidates, but never too few.
        service_minutes = min(effective_service_minutes, default=default_service_minutes)
        reload_minutes = max(0, int(getattr(vehicle_config, "reload_time_minutes", 0) or 0))
        if shift_minutes > 0 and service_minutes + reload_minutes > 0:
            # k * service + (k - 1) * reload <= shift. Travel is ignored so
            # this remains a conservative (never too small) upper bound.
            max_trips_from_shift = max(
                1,
                int((shift_minutes + reload_minutes) // (service_minutes + reload_minutes)),
            )
            bounds.append(max(0, max_trips_from_shift - 1))

        return max(0, min(bounds))

    def _vehicle_key(
        self,
        vehicle_config: Optional[VehicleConfig],
        enabled_config_index: int,
        occurrence: int,
    ) -> str:
        if vehicle_config:
            base = str(getattr(vehicle_config, "config_id", "") or "").strip()
            if not base:
                vehicle_type = getattr(vehicle_config.vehicle_type, "value", vehicle_config.vehicle_type)
                base = f"{vehicle_type}:config:{enabled_config_index + 1}"
        else:
            base = f"vehicle_{enabled_config_index + 1}"
        return f"{base}#{max(1, occurrence)}"

    def _objective_metric(self) -> str:
        metric = str(getattr(self.config, "objective_metric", "distance") or "distance").strip().lower()
        if metric in {"time", "duration", "fastest", "shortest_time"}:
            return "time"
        return "distance"

    def _time_objective_includes_waiting(self) -> bool:
        return self._objective_metric() == "time" and bool(
            getattr(self.config, "time_objective_include_waiting", True)
        )

    def _time_objective_cost_weight(self) -> int:
        return _FULL_WORKDAY_TIME_COST_WEIGHT if self._time_objective_includes_waiting() else 1

    def _objective_penalty_cost(self, penalty_value) -> int:
        penalty = max(0.0, float(penalty_value or 0))
        if self._objective_metric() == "time":
            # Existing penalties are tuned as meter-like costs. In time mode we
            # convert them with an approximate 40 km/h reference speed.
            penalty *= 0.09
            penalty *= self._time_objective_cost_weight()
        return max(0, int(round(penalty)))

    def _objective_arc_cost(self, distance_m: float, duration_s: float) -> int:
        if self._objective_metric() == "time":
            if self._time_objective_includes_waiting():
                # Native duration prices the complete workday. The auxiliary
                # edge-cost channel is reserved for constraint/preference
                # penalties, never for real metres in pure time mode.
                return 0
            return max(0, int(round(float(duration_s or 0))))
        return max(0, int(round(float(distance_m or 0))))

    def _time_windows_enabled(self) -> bool:
        return bool(getattr(self.config, "enable_customer_time_windows", False))

    def _vehicle_start_seconds(self, vehicle_config: Optional[VehicleConfig]) -> int:
        if vehicle_config and hasattr(vehicle_config, "start_time_minutes"):
            minutes = int(getattr(vehicle_config, "start_time_minutes", 0) or 0)
        else:
            minutes = int(getattr(self.config, "global_start_time_minutes", 480) or 480)
        return max(0, minutes) * 60

    def _customer_time_window_seconds(self, customer: Customer) -> Optional[Tuple[int, int]]:
        windows = self._customer_time_windows_seconds(customer)
        return windows[0] if windows else None

    def _customer_time_windows_seconds(self, customer: Customer) -> List[Tuple[int, int]]:
        if not self._time_windows_enabled():
            return []

        windows = customer_time_windows_minutes(customer)
        if not windows:
            return []

        return time_windows_to_seconds(windows)

    def _format_customer_time_windows_for_schedule(self, customer: Customer) -> str:
        windows = customer_time_windows_minutes(customer)
        if not windows:
            if self._time_windows_enabled():
                return "Постоянно"
            return ""
        return format_time_windows_minutes(windows)

    def _time_window_status_for_arrival(
        self,
        arrival_seconds: float,
        customer: Customer,
    ) -> Tuple[float, str]:
        windows = self._customer_time_windows_seconds(customer)
        if not windows:
            return 0.0, ""

        _, wait_seconds, window_index, status = choose_time_window_for_arrival(arrival_seconds, windows)
        if status and len(windows) > 1 and window_index >= 0:
            status = f"{status} (прозорец {window_index + 1})"
        return wait_seconds, status

    def _customer_time_window_domain_seconds(self, customer: Customer) -> Optional[Tuple[int, int]]:
        windows = self._customer_time_windows_seconds(customer)
        if not windows:
            return None
        return windows[0][0], windows[-1][1]

    def solve(self) -> CVRPSolution:
        """
        Решава CVRP проблема използвайки PyVRP.

        Returns:
            CVRPSolution със списък маршрути и метрики
        """
        fallback_reason = self._exact_ortools_fallback_reason()
        if fallback_reason and ORTOOLS_AVAILABLE:
            logger.warning(
                "Using the full OR-Tools solver to enforce %s exactly for this "
                "run, so no feasible customers are lost during post-processing.",
                fallback_reason,
            )
            solution = ORToolsSolver(
                self.config,
                self.vehicle_configs,
                self.customers,
                self.distance_matrix,
                self.unique_depots,
                self.center_zone_customers,
                self.location_config,
            ).solve()
            return self._annotate_solution(
                solution,
                solver_used="or_tools",
                fallback_used=True,
                fallback_reason=fallback_reason,
            )

        if not PYVRP_AVAILABLE:
            logger.error("PyVRP is not installed")
            return self._annotate_solution(self._create_empty_solution())

        try:
            # 1. Създаване на PyVRP модел
            model = self._create_pyvrp_model()
            
            logger.info(f"PyVRP model created successfully")
            logger.info(f"  - Depots: {len(model.depots)}")
            logger.info(f"  - Clients: {len(model.clients)}")
            logger.info(f"  - Vehicle types: {len(model.vehicle_types)}")
            logger.info(f"  - Objective metric: {self._objective_metric()}")

            # 2. Получаваме ProblemData от модела
            problem_data = model.data()

            # 3. Решаване
            seed = getattr(self.config, "pyvrp_seed", None)
            if seed is None:
                seed = getattr(self.config, "pyvrp_seed_base", 42)
            seed = int(seed or 42)
            params = self._create_solve_params()
            display_progress = bool(getattr(self.config, "pyvrp_display_progress", False))
            logger.info(f"Solving with PyVRP (time limit: {self.config.time_limit_seconds}s, seed: {seed})...")
            
            # Използваме MaxRuntime като stop критерий
            stop_criterion = MaxRuntime(self.config.time_limit_seconds)
            result = solve(
                problem_data,
                stop=stop_criterion,
                seed=seed,
                params=params,
                display=display_progress,
            )

            # 4. Обработка на решението
            logger.info(f"PyVRP solution found")
            logger.info(f"  - Internal solver cost: {result.cost()}")
            logger.info(f"  - Feasible: {result.is_feasible()}")

            result_cost = float(result.cost())
            if not result.best or not result.is_feasible() or not math.isfinite(result_cost):
                logger.error(
                    "PyVRP finished without a usable solution "
                    "(has_best=%s, feasible=%s, cost=%s). The invalid routes "
                    "will not be exported.",
                    bool(result.best),
                    bool(result.is_feasible()),
                    result.cost(),
                )
                return self._annotate_solution(self._create_empty_solution())
            
            solution = self._extract_solution(model, problem_data, result)
            logger.info(
                "  - Public fitness score (%s): %s",
                "pure time seconds" if self._objective_metric() == "time" else "distance cost",
                solution.fitness_score,
            )
            violations = self._validate_extracted_solution_constraints(solution)
            if violations:
                logger.error(
                    "PyVRP result failed the independent hard-constraint audit: %s",
                    "; ".join(violations),
                )
                for route in solution.routes:
                    route.is_feasible = False
                solution.is_feasible = False
                solution.fitness_score = float("inf")

            return self._annotate_solution(solution)

        except Exception as e:
            logger.error(f"Error in PyVRP solver: {e}", exc_info=True)
            return self._annotate_solution(self._create_empty_solution())

    def _validate_extracted_solution_constraints(self, solution: CVRPSolution) -> List[str]:
        """Audit reportable routes against configured hard limits.

        PyVRP validates its internal representation, but reporting recomputes
        travel, traffic, waiting and service time from our matrices. This
        second boundary check prevents a solver/API mismatch from ever being
        exported as a valid plan.
        """
        violations: List[str] = []
        routes_by_vehicle: Dict[str, List[Route]] = {}

        for route in solution.routes:
            key = str(getattr(route, "vehicle_key", "") or "").strip()
            if not key:
                vehicle_type = getattr(route.vehicle_type, "value", route.vehicle_type)
                key = f"{vehicle_type}#{route.vehicle_id}"
            routes_by_vehicle.setdefault(key, []).append(route)

        def config_for_route(route: Route) -> Optional[VehicleConfig]:
            key_base = str(getattr(route, "vehicle_key", "") or "").rsplit("#", 1)[0]
            for vehicle in self.vehicle_configs:
                if vehicle.enabled and str(getattr(vehicle, "config_id", "") or "") == key_base:
                    return vehicle

            route_type = getattr(route.vehicle_type, "value", route.vehicle_type)
            for vehicle in self.vehicle_configs:
                vehicle_type = getattr(vehicle.vehicle_type, "value", vehicle.vehicle_type)
                if vehicle.enabled and vehicle_type == route_type:
                    return vehicle
            return None

        # Business IDs are not guaranteed to be unique: separate visits may
        # legitimately share the same external customer number.  Audit visits
        # by the original Customer object identity (and occurrence count)
        # instead, so serving one duplicate ID cannot hide another visit.
        available_visit_counts: Dict[int, int] = {}
        for customer in self.customers:
            identity = id(customer)
            available_visit_counts[identity] = available_visit_counts.get(identity, 0) + 1

        served_visit_counts: Dict[int, int] = {}
        for route in solution.routes:
            for customer in route.customers:
                identity = id(customer)
                served_visit_counts[identity] = served_visit_counts.get(identity, 0) + 1
                if served_visit_counts[identity] > available_visit_counts.get(identity, 0):
                    violations.append(
                        f"клиент {getattr(customer, 'id', '')}: обслужен е повече от веднъж"
                    )

        for vehicle_key, vehicle_routes in routes_by_vehicle.items():
            vehicle_routes.sort(key=lambda route: int(getattr(route, "trip_number", 1) or 1))
            vehicle = config_for_route(vehicle_routes[0])
            if vehicle is None:
                violations.append(f"{vehicle_key}: липсва конфигурация на превозното средство")
                continue

            daily_customers = 0
            daily_distance_km = 0.0
            planned_starts: List[float] = []
            planned_ends: List[float] = []

            for route in vehicle_routes:
                daily_customers += len(route.customers)
                daily_distance_km += float(route.total_distance_km or 0)
                route_volume = sum(float(getattr(customer, "volume", 0) or 0) for customer in route.customers)
                if route_volume > float(vehicle.capacity or 0) + 1e-9:
                    violations.append(
                        f"{vehicle_key}/курс {route.trip_number}: обем {route_volume:.2f} > {vehicle.capacity}"
                    )
                if not bool(getattr(route, "is_feasible", True)):
                    violations.append(f"{vehicle_key}/курс {route.trip_number}: маршрутът е маркиран невалиден")

                if route.planned_start_minutes is not None:
                    planned_starts.append(float(route.planned_start_minutes))
                if route.planned_end_minutes is not None:
                    planned_ends.append(float(route.planned_end_minutes))

                for entry in route.schedule_entries or []:
                    customer = entry.get("customer")
                    if customer is None:
                        continue
                    customer_id = str(getattr(customer, "id", "") or "")

                    if not self._time_windows_enabled():
                        continue
                    windows = self._customer_time_windows_seconds(customer)
                    arrival_seconds = float(entry.get("arrival_time_minutes", 0) or 0) * 60
                    if windows and not any(start <= arrival_seconds <= end for start, end in windows):
                        violations.append(
                            f"{vehicle_key}/клиент {customer_id}: пристигане извън работното време"
                        )

            daily_limit = getattr(vehicle, "max_customers_per_day", None)
            if daily_limit is None:
                daily_limit = getattr(vehicle, "max_customers_per_route", None)
            if daily_limit and daily_customers > int(daily_limit):
                violations.append(
                    f"{vehicle_key}: клиенти за деня {daily_customers} > {int(daily_limit)}"
                )

            max_distance_km = getattr(vehicle, "max_distance_km", None)
            if max_distance_km and daily_distance_km > float(max_distance_km) + 1e-6:
                violations.append(
                    f"{vehicle_key}: разстояние за деня {daily_distance_km:.2f} km > {float(max_distance_km):.2f} km"
                )

            if planned_starts and planned_ends:
                work_minutes = max(planned_ends) - min(planned_starts)
            else:
                work_minutes = sum(float(route.total_time_minutes or 0) for route in vehicle_routes)
                if len(vehicle_routes) > 1:
                    work_minutes += (len(vehicle_routes) - 1) * float(vehicle.reload_time_minutes or 0)
            max_work_minutes = float(vehicle.max_time_hours or 0) * 60
            if (
                max_work_minutes
                and work_minutes
                > max_work_minutes + WORK_TIME_AUDIT_TOLERANCE_MINUTES + 1e-6
            ):
                violations.append(
                    f"{vehicle_key}: работно време {work_minutes:.1f} min > "
                    f"{max_work_minutes:.1f} min (+{WORK_TIME_AUDIT_TOLERANCE_MINUTES:.1f} min толеранс)"
                )

        remaining_served_counts = dict(served_visit_counts)
        mandatory_missing = []
        for customer in self.customers:
            if not is_mandatory_customer(customer):
                continue
            identity = id(customer)
            if remaining_served_counts.get(identity, 0) > 0:
                remaining_served_counts[identity] -= 1
            else:
                mandatory_missing.append(customer)

        if mandatory_missing:
            sample = ", ".join(str(customer.id) for customer in mandatory_missing[:10])
            violations.append(
                f"пропуснати са {len(mandatory_missing)} абсолютно задължителни клиенти: {sample}"
            )
        elif not bool(getattr(self.config, "allow_customer_skipping", True)) and solution.dropped_customers:
            violations.append(
                f"пропуснати са {len(solution.dropped_customers)} задължителни клиенти"
            )

        return violations

    def _create_solve_params(self) -> SolveParams:
        """Builds PyVRP search parameters from CVRPConfig."""
        num_neighbours = int(getattr(self.config, "pyvrp_num_neighbours", 50) or 50)
        ils_no_improvement = int(getattr(self.config, "pyvrp_ils_no_improvement", 150000) or 150000)
        ils_history_length = int(getattr(self.config, "pyvrp_ils_history_length", 300) or 300)
        exhaustive_on_best = bool(getattr(self.config, "pyvrp_exhaustive_on_best", True))
        use_extended_operators = bool(getattr(self.config, "pyvrp_use_extended_operators", False))
        min_perturbations = int(getattr(self.config, "pyvrp_min_perturbations", 1) or 1)
        max_perturbations = int(getattr(self.config, "pyvrp_max_perturbations", 25) or 25)
        weight_wait_time = float(getattr(self.config, "pyvrp_weight_wait_time", 0.2))
        symmetric_proximity = bool(getattr(self.config, "pyvrp_symmetric_proximity", True))
        display_interval = float(getattr(self.config, "pyvrp_display_interval_seconds", 5.0))
        use_library_penalties = bool(
            getattr(self.config, "pyvrp_use_library_penalty_defaults", True)
        )

        if display_interval <= 0:
            raise ValueError("pyvrp_display_interval_seconds must be positive")
        if min_perturbations > max_perturbations:
            raise ValueError(
                "pyvrp_min_perturbations cannot exceed pyvrp_max_perturbations"
            )

        if use_library_penalties:
            penalty_params = PenaltyParams()
        else:
            penalty_params = PenaltyParams(
                solutions_between_updates=int(
                    getattr(self.config, "pyvrp_penalty_solutions_between_updates", 500)
                ),
                penalty_increase=float(
                    getattr(self.config, "pyvrp_penalty_increase", 1.5)
                ),
                penalty_decrease=float(
                    getattr(self.config, "pyvrp_penalty_decrease", 0.9)
                ),
                target_feasible=float(
                    getattr(self.config, "pyvrp_penalty_target_feasible", 0.65)
                ),
                feas_tolerance=float(
                    getattr(self.config, "pyvrp_penalty_feas_tolerance", 0.05)
                ),
                min_penalty=float(getattr(self.config, "pyvrp_penalty_min", 0.1)),
                max_penalty=float(
                    getattr(self.config, "pyvrp_penalty_max", 100000.0)
                ),
            )

        node_ops = [
            Exchange10,
            Exchange20,
            Exchange11,
            Exchange21,
            Exchange22,
            SwapTails,
            RelocateWithDepot,
        ]
        route_ops = []
        if use_extended_operators:
            node_ops.extend([Exchange30, Exchange31, Exchange32, Exchange33])
            route_ops.extend(
                operator
                for operator in (SwapStar, SwapRoutes)
                if operator is not None
            )

        common_params = {
            "ils": IteratedLocalSearchParams(
                num_iters_no_improvement=ils_no_improvement,
                history_length=ils_history_length,
                exhaustive_on_best=exhaustive_on_best,
            ),
            "penalty": penalty_params,
            "neighbourhood": NeighbourhoodParams(
                weight_wait_time=weight_wait_time,
                num_neighbours=num_neighbours,
                symmetric_proximity=symmetric_proximity,
            ),
            "display_interval": display_interval,
            "perturbation": PerturbationParams(
                min_perturbations=min_perturbations,
                max_perturbations=max_perturbations,
            ),
        }
        if PYVRP_LOCATION_API:
            # The 0.14 search API has one combined operator list and no
            # separate route-operator hierarchy.  Its defaults also include
            # optional/group replacement operators that are essential for
            # alternative time-window clients.
            operators = list(PYVRP_DEFAULT_OPERATORS or node_ops)
            if use_extended_operators:
                for operator in (Exchange30, Exchange31, Exchange32, Exchange33):
                    if operator not in operators:
                        operators.append(operator)
            params = SolveParams(operators=operators, **common_params)
            node_ops = operators
            route_ops = []
        else:
            params = SolveParams(
                node_ops=node_ops,
                route_ops=route_ops,
                **common_params,
            )

        logger.info("PyVRP search params:")
        logger.info(f"  - num_neighbours: {num_neighbours}")
        logger.info(f"  - weight_wait_time: {weight_wait_time}")
        logger.info(f"  - symmetric_proximity: {symmetric_proximity}")
        logger.info(f"  - ils_no_improvement: {ils_no_improvement}")
        logger.info(f"  - ils_history_length: {ils_history_length}")
        logger.info(f"  - exhaustive_on_best: {exhaustive_on_best}")
        logger.info(f"  - perturbations: {min_perturbations}..{max_perturbations}")
        logger.info(f"  - display_interval_seconds: {display_interval}")
        logger.info(
            "  - penalty_params: %s",
            "library defaults"
            if use_library_penalties
            else {
                "solutions_between_updates": penalty_params.solutions_between_updates,
                "penalty_increase": penalty_params.penalty_increase,
                "penalty_decrease": penalty_params.penalty_decrease,
                "target_feasible": penalty_params.target_feasible,
                "feas_tolerance": penalty_params.feas_tolerance,
                "min_penalty": penalty_params.min_penalty,
                "max_penalty": penalty_params.max_penalty,
            },
        )
        logger.info(f"  - node_ops: {[op.__name__ for op in node_ops]}")
        logger.info(f"  - route_ops: {[op.__name__ for op in route_ops]}")
        return params

    def _create_pyvrp_model(self) -> Model:
        """
        Създава PyVRP модел от нашите структури.

        Returns:
            PyVRP Model инстанция
        """
        model = Model()
        enabled_vehicles = [v for v in self.vehicle_configs if v.enabled]
        
        # Всички локации (депа + клиенти) в списък за лесен достъп
        all_locations = []  # List of (depot/client objects)
        self._pyvrp_client_to_customer_idx = []
        self._pyvrp_location_matrix_indices = []
        
        # 1. Добавяме депа
        depot_objects = []  # За референция към depot обекти
        depot_to_obj = {}
        reload_depot_to_obj = {}
        reload_service_by_location_index: Dict[int, int] = {}
        self._pyvrp_depot_coords = []
        logger.info(f"Adding {len(self.unique_depots)} depots to model:")
        for i, (lat, lon) in enumerate(self.unique_depots):
            depot, edge_location = self._add_model_depot(
                model,
                x=lon,
                y=lat,
                name=f"Depot_{i}",
            )
            depot_objects.append(depot)
            depot_to_obj[(lat, lon)] = depot
            all_locations.append(edge_location)
            self._pyvrp_location_matrix_indices.append(i)
            self._pyvrp_depot_coords.append((lat, lon))
            logger.info(f"  - Depot {i}: ({lat}, {lon})")

        # Reload service must only be charged between trips. Reusing the normal
        # start depot would also charge it before trip one, so every distinct
        # (location, reload duration) gets a duplicate depot at the same matrix
        # coordinate with depot-level service duration.
        if self._multiple_trips_enabled() and self.unique_depots:
            for v_config in enabled_vehicles:
                reload_location = (
                    getattr(v_config, "reload_location", None)
                    or getattr(v_config, "start_location", None)
                    or self.unique_depots[0]
                )
                reload_matrix_idx = self._get_depot_index_for_location(reload_location, 0)
                reload_coords = self.unique_depots[reload_matrix_idx]
                reload_seconds = max(
                    0,
                    int(getattr(v_config, "reload_time_minutes", 0) or 0) * 60,
                )
                reload_key = (reload_matrix_idx, reload_seconds)
                if reload_key in reload_depot_to_obj:
                    continue

                lat, lon = reload_coords
                reload_depot, edge_location = self._add_model_depot(
                    model,
                    x=lon,
                    y=lat,
                    service_duration=reload_seconds,
                    name=f"Reload_{reload_matrix_idx}_{reload_seconds}",
                )
                reload_depot_to_obj[reload_key] = reload_depot
                reload_service_by_location_index[len(depot_objects)] = reload_seconds
                depot_objects.append(reload_depot)
                all_locations.append(edge_location)
                self._pyvrp_location_matrix_indices.append(reload_matrix_idx)
                self._pyvrp_depot_coords.append(reload_coords)
                logger.info(
                    "  - Reload depot: coords=%s, service=%s min",
                    reload_coords,
                    reload_seconds / 60,
                )

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
            delivery = int(round(float(customer.volume or 0) * 100)) if hasattr(customer, 'volume') and customer.volume else 1
            
            # Проверяваме дали клиентът е в център зоната
            is_in_center = customer.id in center_zone_customer_ids
            
            # Prize пропорционална на обема - по-големи клиенти са по-важни
            # Ако allow_dropping=False, клиентите са required=True
            customer_required = (not allow_dropping) or is_mandatory_customer(customer)
            client_prize = (
                self._objective_penalty_cost(drop_penalties[idx])
                if allow_dropping and not customer_required
                else 0
            )
            time_windows = self._customer_time_windows_seconds(customer)
            if not time_windows:
                time_windows = [None]

            client_group = None
            use_alternatives = len(time_windows) > 1
            if use_alternatives:
                client_group = model.add_client_group(
                    required=customer_required,
                    name=f"ClientGroup_{customer.id}",
                )

            for window_idx, time_window in enumerate(time_windows):
                time_window_kwargs = {}
                if time_window:
                    time_window_kwargs = {
                        "tw_early": int(time_window[0]),
                        "tw_late": int(time_window[1]),
                    }

                # При няколко прозореца създаваме алтернативни клиенти в mutually exclusive group.
                client, edge_location = self._add_model_client(
                    model,
                    x=lon,
                    y=lat,
                    delivery=[delivery, 1],  # [volume, 1 спирка]
                    service_duration=service_duration_s,
                    prize=client_prize,
                    required=(customer_required and not use_alternatives),
                    group=client_group,
                    name=f"Client_{customer.id}_tw{window_idx + 1}",
                    **time_window_kwargs,
                )
                client_objects.append(client)
                all_locations.append(edge_location)
                client_in_center.append(is_in_center)
                self._pyvrp_client_to_customer_idx.append(idx)
                self._pyvrp_location_matrix_indices.append(len(self.unique_depots) + idx)

            logger.debug(
                "  - Client %s: ID=%s, volume=%s, center=%s, prize=%s, windows=%s",
                idx,
                customer.id,
                delivery,
                is_in_center,
                client_prize,
                len(time_windows) if time_windows != [None] else 0,
            )

        # 4. Създаваме профил за всеки тип бус, за да има точен service time.
        def profile_signature(v_config: VehicleConfig):
            service_time_seconds = int(v_config.service_time_minutes * 60)
            vehicle_type_value = getattr(v_config.vehicle_type, "value", str(v_config.vehicle_type))

            # Service time affects shift duration and time-window feasibility,
            # so it is part of the profile signature even in distance mode.
            return (
                service_time_seconds,
                vehicle_type_value,
                center_zone_profile_signature(vehicle_type_value, self.location_config),
            )

        compressed_profiles = {}
        profile_specs = []
        vehicle_profiles_by_config_index = {}
        vehicle_profile_names_by_config_index = {}

        for enabled_idx, v_config in enumerate(enabled_vehicles):
            signature = profile_signature(v_config)
            if signature not in compressed_profiles:
                spec = {
                    "profile": model.add_profile(),
                    "service_time_seconds": signature[0],
                    "vehicle_type": signature[1],
                    "center_behavior": signature[2],
                    "vehicle_types": [],
                }
                compressed_profiles[signature] = spec
                profile_specs.append(spec)

            spec = compressed_profiles[signature]
            spec["vehicle_types"].append(v_config.vehicle_type.value)
            vehicle_profiles_by_config_index[enabled_idx] = spec["profile"]
            vehicle_profile_names_by_config_index[enabled_idx] = (
                f"profile#{profile_specs.index(spec) + 1} {signature}"
            )

        logger.info(
            "PyVRP profile compression: %s vehicle configs -> %s shared profiles",
            len(enabled_vehicles),
            len(profile_specs),
        )
        for idx, spec in enumerate(profile_specs, start=1):
            logger.info(
                "  - profile #%s: service=%s sec, center_rules=%s, vehicles=%s",
                idx,
                spec["service_time_seconds"],
                spec["center_behavior"],
                ", ".join(spec["vehicle_types"]),
            )

        # 5. Добавяме edges за всички профили
        num_locations = len(all_locations)
        num_depots = len(depot_objects)
        objective_metric = self._objective_metric()
        logger.info(f"Adding edges with center zone logic...")
        logger.info(f"  - Objective metric: {objective_metric}")
        if self._time_objective_includes_waiting():
            logger.info(
                "PyVRP pure full-workday time objective ENABLED: fitness uses only "
                "travel, service, waiting and reload duration in seconds; real metres "
                "have zero objective cost and remain available for limits/reporting."
            )
        elif objective_metric == "time":
            logger.warning(
                "PyVRP time objective uses duration as PyVRP distance/cost; real km "
                "are reported separately, and configured km caps use the exact OR-Tools fallback."
            )
        logger.info("  - Center zone rules are applied per independent zone and per vehicle type")
        
        # Настройки за градски трафик
        traffic_zones = get_traffic_zones(self.location_config)
        location_coords = []
        for loc_idx, matrix_idx in enumerate(self._pyvrp_location_matrix_indices):
            if matrix_idx < len(self.unique_depots):
                coords = self.unique_depots[matrix_idx]
            else:
                client_idx = matrix_idx - len(self.unique_depots)
                coords = (
                    self.customers[client_idx].coordinates
                    if 0 <= client_idx < len(self.customers) and self.customers[client_idx].coordinates
                    else (0, 0)
                )
            location_coords.append(coords)

        if traffic_zones:
            logger.info(f"City traffic adjustment ENABLED: {len(traffic_zones)} zones")
            for zone in traffic_zones:
                logger.info(
                    "  - %s: center=%s radius=%skm multiplier=%s",
                    zone.name,
                    zone.center_coords,
                    zone.radius_km,
                    zone.duration_multiplier,
                )
        
        city_edges_count = 0
        for i in range(num_locations):
            for j in range(num_locations):
                if i == j:
                    continue
                
                # Базово разстояние и време от OSRM матрицата
                matrix_i = self._pyvrp_location_matrix_indices[i]
                matrix_j = self._pyvrp_location_matrix_indices[j]
                base_distance = int(self.distance_matrix.distances[matrix_i][matrix_j])
                duration = int(self.distance_matrix.durations[matrix_i][matrix_j])
                
                # Прилагаме множител за градски трафик ако отсечката попада в активна зона.
                traffic_multiplier = get_traffic_multiplier(
                    self.location_config,
                    location_coords[i],
                    location_coords[j],
                )
                if traffic_multiplier > 1.0:
                    duration = int(duration * traffic_multiplier)
                    city_edges_count += 1
                reload_service_seconds = reload_service_by_location_index.get(i, 0)
                base_objective_cost = self._objective_arc_cost(
                    base_distance,
                    duration + reload_service_seconds,
                )
                
                # Проверяваме дали destination е клиент в центъра
                is_dest_client = j >= num_depots
                is_dest_center_client = False
                if is_dest_client:
                    client_idx = j - num_depots
                    if client_idx < len(client_in_center):
                        is_dest_center_client = client_in_center[client_idx]
                
                # Базов edge (без профил) - без service time.
                model.add_edge(
                    frm=all_locations[i],
                    to=all_locations[j],
                    distance=base_objective_cost,
                    duration=duration
                )

                for profile_spec in profile_specs:
                    vehicle_type_value = profile_spec["vehicle_type"]
                    vehicle_duration = duration
                    if i >= num_depots:
                        customer_index = matrix_i - len(self.unique_depots)
                        service_override = (
                            getattr(self.customers[customer_index], "service_time_minutes", None)
                            if 0 <= customer_index < len(self.customers)
                            else None
                        )
                        if service_override is None or (
                            isinstance(service_override, str) and not service_override.strip()
                        ):
                            service_seconds = int(profile_spec["service_time_seconds"])
                        else:
                            try:
                                service_seconds = max(
                                    0,
                                    int(round(float(service_override) * 60)),
                                )
                            except (TypeError, ValueError, OverflowError):
                                service_seconds = int(profile_spec["service_time_seconds"])
                        vehicle_duration += service_seconds
                    profile_objective_cost = self._objective_arc_cost(
                        base_distance,
                        vehicle_duration + reload_service_seconds,
                    )

                    vehicle_distance = profile_objective_cost
                    if is_dest_client:
                        multiplier, penalty = center_zone_cost_adjustment(
                            location_coords[j],
                            vehicle_type_value,
                            self.location_config,
                            self._objective_penalty_cost,
                        )
                        vehicle_distance = int(round(profile_objective_cost * multiplier)) + penalty

                    model.add_edge(
                        frm=all_locations[i],
                        to=all_locations[j],
                        distance=vehicle_distance,
                        duration=vehicle_duration,
                        profile=profile_spec["profile"]
                    )

        if traffic_zones:
            logger.info(f"City traffic adjustment applied:")
            logger.info(f"  - Active traffic zones: {len(traffic_zones)}")
            logger.info(f"  - Edges with traffic multiplier: {city_edges_count}")

        # 6. Добавяме типове превозни средства с правилния профил
        def resolve_depot(location, default_depot, label):
            if not location:
                return default_depot
            if location in depot_to_obj:
                return depot_to_obj[location]
            for depot_coords, depot_obj in depot_to_obj.items():
                if (
                    abs(float(depot_coords[0]) - float(location[0])) < 0.000001
                    and abs(float(depot_coords[1]) - float(location[1])) < 0.000001
                ):
                    return depot_obj
            logger.warning(f"⚠️ Depot {location} not found for {label}, using default depot")
            return default_depot

        vehicle_id = 0
        for enabled_idx, v_config in enumerate(enabled_vehicles):

            # Определяме депо за този тип превозно средство
            default_depot = depot_objects[0] if depot_objects else None
            start_depot = resolve_depot(v_config.start_location, default_depot, v_config.vehicle_type.value)
            end_depot = resolve_depot(getattr(v_config, "end_location", None), start_depot, v_config.vehicle_type.value)

            # Преобразуваме ограниченията
            capacity = int(round(float(v_config.capacity or 0) * 100))
            max_distance = int(v_config.max_distance_km * 1000) if v_config.max_distance_km else constants.MAX_VALUE
            if self._objective_metric() == "time":
                max_distance = constants.MAX_VALUE
            max_time = int(v_config.max_time_hours * 3600)  # в секунди
            raw_fixed_cost = int(getattr(v_config, "fixed_cost", 0) or 0)
            fixed_cost = self._objective_penalty_cost(raw_fixed_cost)
            vehicle_time_kwargs = {}
            if self._time_windows_enabled():
                vehicle_start = self._vehicle_start_seconds(v_config)
                vehicle_time_kwargs = {
                    "tw_early": vehicle_start,
                    "tw_late": vehicle_start + max_time,
                    "start_late": vehicle_start,
                }

            reload_kwargs = {}
            if self._multiple_trips_enabled():
                reload_location = (
                    getattr(v_config, "reload_location", None)
                    or getattr(v_config, "start_location", None)
                    or self.unique_depots[0]
                )
                reload_matrix_idx = self._get_depot_index_for_location(reload_location, 0)
                reload_seconds = max(
                    0,
                    int(getattr(v_config, "reload_time_minutes", 0) or 0) * 60,
                )
                max_reloads = self._dynamic_max_reloads(v_config)
                reload_depot = reload_depot_to_obj.get((reload_matrix_idx, reload_seconds))
                if reload_depot is not None:
                    reload_kwargs = {
                        "reload_depots": [reload_depot],
                        "max_reloads": max_reloads,
                    }
            
            # With one trip the daily limit can be modelled natively.  With
            # reloads PyVRP resets load dimensions per trip, so the cumulative
            # daily limit is enforced during extraction instead of becoming an
            # incorrect per-trip restriction.
            daily_customer_limit = getattr(v_config, "max_customers_per_day", None)
            if daily_customer_limit is None:
                daily_customer_limit = getattr(v_config, "max_customers_per_route", None)
            max_customers = (
                1000
                if self._multiple_trips_enabled()
                else (int(daily_customer_limit) if daily_customer_limit else 1000)
            )

            vehicle_profile = vehicle_profiles_by_config_index[enabled_idx]
            profile_name = vehicle_profile_names_by_config_index[enabled_idx]
            profile_name = f"{v_config.vehicle_type.value} ({v_config.service_time_minutes} мин service)"

            # Model.add_vehicle_type с двумерен capacity: [volume, max_stops]
            model.add_vehicle_type(
                num_available=v_config.count,
                capacity=[capacity, max_customers],  # [volume_capacity, max_customers]
                start_depot=start_depot,
                end_depot=end_depot,
                max_distance=max_distance,
                shift_duration=max_time,
                fixed_cost=fixed_cost,
                unit_distance_cost=1,
                unit_duration_cost=(
                    self._time_objective_cost_weight()
                    if self._time_objective_includes_waiting()
                    else 0
                ),
                profile=vehicle_profile,
                name=f"{v_config.vehicle_type.value}",
                **vehicle_time_kwargs,
                **reload_kwargs,
            )
            
            logger.info(f"Vehicle type: {v_config.vehicle_type.value}")
            logger.info(f"  - Count: {v_config.count}")
            logger.info(f"  - Capacity: {v_config.capacity}")
            logger.info(f"  - Max customers: {max_customers}")
            logger.info(f"  - Max distance: {v_config.max_distance_km}km")
            logger.info(f"  - Max time: {v_config.max_time_hours}h")
            logger.info(f"  - Fixed cost: {fixed_cost}")
            profile_name = vehicle_profile_names_by_config_index[enabled_idx]
            logger.info(f"  - Profile: {profile_name}")
            logger.info(f"  - Start depot: {start_depot}")
            logger.info(f"  - End depot: {end_depot}")
            if reload_kwargs:
                logger.info(
                    "  - Dynamic trips: reload=%s, reload time=%s min, internal max reloads=%s",
                    reload_location,
                    reload_seconds / 60,
                    reload_kwargs["max_reloads"],
                )

            vehicle_id += 1

        logger.info(f"PyVRP model created successfully")
        logger.info(f"  - Depots: {len(model.depots)}")
        logger.info(f"  - Clients: {len(model.clients)}")
        logger.info(f"  - Vehicle types: {len(model.vehicle_types)}")
        logger.info(f"  - Center zone customers: {len(center_zone_customer_ids)}")

        return model


    def _extract_solution_legacy(self, model: Model, problem_data, result) -> CVRPSolution:
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
        dropped_customer_indices = set(range(len(self.customers)))
        total_distance_km = 0.0
        total_time_minutes = 0.0
        total_volume = 0.0

        if not result.best or not result.best.routes():
            logger.warning("PyVRP vrati praznoto reshenie")
            return self._create_empty_solution()
        
        # Вземаме списъка с vehicle types за да можем да ги индексираме
        vehicle_types_list = problem_data.vehicle_types()
        vehicle_type_offsets = {}
        next_vehicle_id = 0
        for vt_idx, vt in enumerate(vehicle_types_list):
            vehicle_type_offsets[vt_idx] = next_vehicle_id
            next_vehicle_id += int(getattr(vt, "num_available", 1) or 1)
        vehicle_type_usage = {}

        # Iteriraem prez vseki marshut v resheniyeto
        for route_idx, route in enumerate(result.best.routes()):
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
                if 0 <= client_idx < len(self._pyvrp_client_to_customer_idx):
                    original_customer_idx = self._pyvrp_client_to_customer_idx[client_idx]
                    customer = self.customers[original_customer_idx]
                    route_customers.append(customer)
                    dropped_customer_indices.discard(original_customer_idx)
                    route_volume += customer.volume if hasattr(customer, 'volume') and customer.volume else 0

            # Izchislyavaem razstoyanie i vreme za marshuta
            if route_customers:
                # Namirame depoto za tozi marshut чрез vehicle_type
                vt_index = None
                try:
                    vt_index = route.vehicle_type()  # Връща индекс (int)
                    vt = vehicle_types_list[vt_index] if vt_index < len(vehicle_types_list) else None
                    # vt.start_depot/end_depot са индексите на депата в модела.
                    depot_idx = vt.start_depot if vt else 0
                    end_depot_idx = getattr(vt, "end_depot", depot_idx) if vt else depot_idx
                except Exception as e:
                    logger.warning(f"Could not get depot from route: {e}")
                    depot_idx = 0
                    end_depot_idx = 0
                
                if depot_idx < len(self.unique_depots):
                    depot_location = self.unique_depots[depot_idx]
                else:
                    depot_location = self.unique_depots[0]

                if end_depot_idx < len(self.unique_depots):
                    end_location = self.unique_depots[end_depot_idx]
                else:
                    end_location = depot_location

                # Opredelyame tipa prevozno sredstvo ot marshuta
                vehicle_config = self._get_vehicle_config_for_type_index(vt_index)
                if vehicle_config:
                    vehicle_type = vehicle_config.vehicle_type
                else:
                    vehicle_type = self._get_vehicle_type_from_route(route, vehicle_types_list)
                    vehicle_config = self._get_vehicle_config_for_type(vehicle_type)
                vehicle_id = route_idx
                vehicle_occurrence = 1
                if isinstance(vt_index, int):
                    used_count = vehicle_type_usage.get(vt_index, 0)
                    vehicle_occurrence = used_count + 1
                    vehicle_id = vehicle_type_offsets.get(vt_index, route_idx) + used_count
                    vehicle_type_usage[vt_index] = used_count + 1

                # Build the same schedule that reports and maps will display.
                schedule_entries, route_distance_m, route_time_s = self._build_route_schedule_entries(
                    route_customers, depot_location, vehicle_config, end_location
                )
                
                total_distance_km += route_distance_m / 1000
                total_time_minutes += route_time_s / 60
                total_volume += route_volume

                route_obj = Route(
                    vehicle_type=vehicle_type,
                    vehicle_id=vehicle_id,
                    customers=route_customers,
                    depot_location=depot_location,
                    end_location=end_location,
                    vehicle_name=self._get_vehicle_display_name(vehicle_config, vehicle_occurrence),
                    total_distance_km=route_distance_m / 1000,
                    total_time_minutes=route_time_s / 60,
                    total_volume=route_volume,
                    is_feasible=True,
                    schedule_entries=schedule_entries
                )
                routes_list.append(route_obj)

        # Sobiramy propusnatite klienti
        dropped_customers_list = [
            customer
            for idx, customer in enumerate(self.customers)
            if idx in dropped_customer_indices
        ]

        # Sazdavaame resheniyeto
        solution = CVRPSolution(
            routes=routes_list,
            dropped_customers=dropped_customers_list,
            total_distance_km=total_distance_km,
            total_time_minutes=total_time_minutes,
            total_vehicles_used=len(routes_list),
            fitness_score=(
                float(total_time_minutes * 60)
                if self._objective_metric() == "time"
                else float(result.cost())
            ),
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

    def _extract_solution(self, model: Model, problem_data, result) -> CVRPSolution:
        """Flattens native PyVRP routes/trips into reportable physical courses."""
        routes_list: List[Route] = []
        dropped_customer_indices = set(range(len(self.customers)))
        total_distance_km = 0.0
        total_time_minutes = 0.0
        total_volume = 0.0
        total_physical_vehicles = 0
        postprocess_clipped = False

        if not result.best or not result.best.routes():
            logger.warning("PyVRP vrati praznoto reshenie")
            return self._create_empty_solution()

        vehicle_types_list = problem_data.vehicle_types()
        vehicle_type_offsets = {}
        next_vehicle_id = 0
        for vt_idx, vt in enumerate(vehicle_types_list):
            vehicle_type_offsets[vt_idx] = next_vehicle_id
            next_vehicle_id += int(getattr(vt, "num_available", 1) or 1)
        vehicle_type_usage: Dict[int, int] = {}
        num_model_depots = len(model.depots)

        def depot_coords(depot_idx: int, fallback: Tuple[float, float]) -> Tuple[float, float]:
            if 0 <= depot_idx < len(self._pyvrp_depot_coords):
                return self._pyvrp_depot_coords[depot_idx]
            return fallback

        def customer_indices_from_legacy_visits(visits) -> List[int]:
            customer_indices: List[int] = []
            for visit_idx in visits:
                if visit_idx < num_model_depots:
                    continue
                client_idx = visit_idx - num_model_depots
                if 0 <= client_idx < len(self._pyvrp_client_to_customer_idx):
                    original_idx = self._pyvrp_client_to_customer_idx[client_idx]
                    customer_indices.append(original_idx)
            return customer_indices

        def activity_value(activity, name: str, default=None):
            value = getattr(activity, name, default)
            return value() if callable(value) else value

        def route_trip_records(pyvrp_route, vehicle_type_data):
            """Normalise 0.13 Trips and 0.14 scheduled Activities.

            In 0.14 every depot activity increments ``activity.trip``.  The
            boundary depot therefore closes the preceding course and starts
            the next one.  Splitting on depot activities preserves both ends
            even though the old ``Route.trips()`` API no longer exists.
            """
            default_start = int(getattr(vehicle_type_data, "start_depot", 0) or 0)
            default_end = int(getattr(vehicle_type_data, "end_depot", default_start) or default_start)

            if PYVRP_LOCATION_API:
                records = []
                current = {
                    "customers": [],
                    "customer_indices": [],
                    "start_idx": default_start,
                    "end_idx": default_end,
                }
                for activity in pyvrp_route:
                    is_depot = bool(activity_value(activity, "is_depot", False))
                    is_client = bool(activity_value(activity, "is_client", False))
                    activity_idx = int(activity_value(activity, "idx", -1) or 0)

                    if is_depot:
                        if current["customers"]:
                            current["end_idx"] = activity_idx
                            records.append(current)
                        current = {
                            "customers": [],
                            "customer_indices": [],
                            "start_idx": activity_idx,
                            "end_idx": default_end,
                        }
                    elif is_client and 0 <= activity_idx < len(self._pyvrp_client_to_customer_idx):
                        original_idx = self._pyvrp_client_to_customer_idx[activity_idx]
                        current["customers"].append(self.customers[original_idx])
                        current["customer_indices"].append(original_idx)

                if current["customers"]:
                    records.append(current)
                return records

            if self._multiple_trips_enabled():
                try:
                    raw_trips = list(pyvrp_route.trips())
                except Exception:
                    raw_trips = [pyvrp_route]
            else:
                raw_trips = [pyvrp_route]

            records = []
            for raw_trip in raw_trips:
                try:
                    visits = raw_trip.visits()
                except Exception:
                    visits = []
                try:
                    start_idx = int(raw_trip.start_depot())
                except Exception:
                    start_idx = default_start
                try:
                    end_idx = int(raw_trip.end_depot())
                except Exception:
                    end_idx = default_end
                customer_indices = customer_indices_from_legacy_visits(visits)
                records.append({
                    "customers": [self.customers[idx] for idx in customer_indices],
                    "customer_indices": customer_indices,
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                })
            return records

        for route_idx, pyvrp_route in enumerate(result.best.routes()):
            try:
                vt_index = int(pyvrp_route.vehicle_type())
                vt = vehicle_types_list[vt_index]
            except Exception as exc:
                logger.warning("Could not determine PyVRP vehicle type: %s", exc)
                continue

            vehicle_config = self._get_vehicle_config_for_type_index(vt_index)
            if vehicle_config:
                vehicle_type = vehicle_config.vehicle_type
            else:
                vehicle_type = self._get_vehicle_type_from_route(pyvrp_route, vehicle_types_list)
                vehicle_config = self._get_vehicle_config_for_type(vehicle_type)

            prepared_trips = []
            remaining_daily_customers = None
            daily_limit = getattr(vehicle_config, "max_customers_per_day", None) if vehicle_config else None
            if daily_limit is None and vehicle_config:
                daily_limit = getattr(vehicle_config, "max_customers_per_route", None)
            if daily_limit is not None and int(daily_limit) > 0:
                remaining_daily_customers = int(daily_limit)

            for trip_record in route_trip_records(pyvrp_route, vt):
                trip_customers = list(trip_record["customers"])
                trip_customer_indices = list(trip_record["customer_indices"])
                if remaining_daily_customers is not None:
                    kept = trip_customers[:remaining_daily_customers]
                    if len(kept) != len(trip_customers):
                        postprocess_clipped = True
                    trip_customers = kept
                    trip_customer_indices = trip_customer_indices[:remaining_daily_customers]
                    remaining_daily_customers = max(
                        0,
                        remaining_daily_customers - len(trip_customers),
                    )

                trip_start_idx = int(trip_record["start_idx"])
                trip_end_idx = int(trip_record["end_idx"])

                fallback_start = self.unique_depots[0]
                start_location = depot_coords(trip_start_idx, fallback_start)
                end_location = depot_coords(trip_end_idx, start_location)
                prepared_trips.append({
                    "customers": trip_customers,
                    "customer_indices": trip_customer_indices,
                    "start_idx": trip_start_idx,
                    "end_idx": trip_end_idx,
                    "start_location": start_location,
                    "end_location": end_location,
                })

            # In time mode PyVRP's auxiliary distance field stores objective
            # costs (legacy duration costs or hybrid metres with zone policy
            # adjustments), so it is not an exact physical-kilometre dimension.
            # Keep the cumulative physical-day distance hard by safely dropping
            # trailing visits until the extracted day fits the configured cap.
            max_distance_km = getattr(vehicle_config, "max_distance_km", None) if vehicle_config else None
            if self._multiple_trips_enabled() and max_distance_km:
                trip_distances = [
                    self._calculate_route_metrics(
                        item["customers"],
                        item["start_location"],
                        vehicle_config,
                        item["end_location"],
                    )[0]
                    if item["customers"] else 0.0
                    for item in prepared_trips
                ]
                max_distance_m = float(max_distance_km) * 1000
                while sum(trip_distances) > max_distance_m:
                    last_idx = next(
                        (
                            idx
                            for idx in range(len(prepared_trips) - 1, -1, -1)
                            if prepared_trips[idx]["customers"]
                        ),
                        None,
                    )
                    if last_idx is None:
                        break
                    prepared_trips[last_idx]["customers"].pop()
                    prepared_trips[last_idx]["customer_indices"].pop()
                    postprocess_clipped = True
                    item = prepared_trips[last_idx]
                    trip_distances[last_idx] = (
                        self._calculate_route_metrics(
                            item["customers"],
                            item["start_location"],
                            vehicle_config,
                            item["end_location"],
                        )[0]
                        if item["customers"] else 0.0
                    )

            prepared_trips = [item for item in prepared_trips if item["customers"]]
            if not prepared_trips:
                continue

            used_count = vehicle_type_usage.get(vt_index, 0)
            vehicle_occurrence = used_count + 1
            vehicle_id = vehicle_type_offsets.get(vt_index, route_idx) + used_count
            vehicle_type_usage[vt_index] = vehicle_occurrence
            total_physical_vehicles += 1
            vehicle_key = self._vehicle_key(vehicle_config, vt_index, vehicle_occurrence)
            trip_count = len(prepared_trips)
            day_clock_seconds = float(self._vehicle_start_seconds(vehicle_config))

            for trip_number, item in enumerate(prepared_trips, start=1):
                if trip_number > 1:
                    try:
                        if PYVRP_LOCATION_API:
                            reload_activity = problem_data.depot(item["start_idx"])
                        else:
                            reload_activity = problem_data.location(item["start_idx"])
                        reload_seconds = float(reload_activity.service_duration)
                    except Exception:
                        reload_seconds = float(
                            max(
                                0,
                                int(getattr(vehicle_config, "reload_time_minutes", 0) or 0),
                            )
                            * 60
                        )
                    day_clock_seconds += reload_seconds
                    total_time_minutes += reload_seconds / 60

                planned_start_minutes = day_clock_seconds / 60
                schedule_entries, route_distance_m, route_time_s = self._build_route_schedule_entries(
                    item["customers"],
                    item["start_location"],
                    vehicle_config,
                    item["end_location"],
                    start_time_seconds=day_clock_seconds,
                )
                day_clock_seconds += route_time_s
                planned_end_minutes = day_clock_seconds / 60
                route_volume = sum(
                    float(getattr(customer, "volume", 0) or 0)
                    for customer in item["customers"]
                )

                for original_idx in item["customer_indices"]:
                    dropped_customer_indices.discard(original_idx)

                total_distance_km += route_distance_m / 1000
                total_time_minutes += route_time_s / 60
                total_volume += route_volume
                route_id = f"{vehicle_key}:trip:{trip_number}"
                routes_list.append(Route(
                    vehicle_type=vehicle_type,
                    vehicle_id=vehicle_id,
                    customers=item["customers"],
                    depot_location=item["start_location"],
                    end_location=item["end_location"],
                    vehicle_name=self._get_vehicle_display_name(vehicle_config, vehicle_occurrence),
                    total_distance_km=route_distance_m / 1000,
                    total_time_minutes=route_time_s / 60,
                    total_volume=route_volume,
                    is_feasible=True,
                    schedule_entries=schedule_entries,
                    vehicle_key=vehicle_key,
                    trip_number=trip_number,
                    trip_count=trip_count,
                    route_id=route_id,
                    planned_start_minutes=planned_start_minutes,
                    planned_end_minutes=planned_end_minutes,
                ))

        dropped_customers_list = [
            customer
            for idx, customer in enumerate(self.customers)
            if idx in dropped_customer_indices
        ]
        allow_dropping = bool(getattr(self.config, "allow_customer_skipping", True))
        if postprocess_clipped and not allow_dropping:
            logger.error(
                "PyVRP post-processing would remove required customers; rejecting "
                "the partial result instead of returning it as a valid route plan."
            )
            return self._create_empty_solution()
        solution = CVRPSolution(
            routes=routes_list,
            dropped_customers=dropped_customers_list,
            total_distance_km=total_distance_km,
            total_time_minutes=total_time_minutes,
            total_vehicles_used=total_physical_vehicles,
            fitness_score=(
                float(total_time_minutes * 60)
                if self._objective_metric() == "time"
                else float(result.cost())
            ),
            is_feasible=bool(result.is_feasible()) and (allow_dropping or not postprocess_clipped),
            total_served_volume=total_volume,
            total_trips=len(routes_list),
            second_trips_count=sum(1 for route in routes_list if route.trip_number > 1),
        )

        logger.info("Reshenie obraboteno:")
        logger.info("  - Fizicheski prevozni sredstva: %s", total_physical_vehicles)
        logger.info("  - Kursove: %s", len(routes_list))
        logger.info("  - Vtori i sledvashti kursove: %s", solution.second_trips_count)
        logger.info("  - Propushcheni klienti: %s", len(dropped_customers_list))
        logger.info("  - Obshto razstoyanie: %.1f km", total_distance_km)
        logger.info("  - Obshto vreme: %.1f min", total_time_minutes)
        logger.info("  - Obsluzhden obem: %.1f", total_volume)
        return solution

    def _get_depot_index_for_location(self, location: Optional[Tuple[float, float]], fallback_index: int = 0) -> int:
        if location:
            for index, depot in enumerate(self.unique_depots):
                if (
                    abs(float(depot[0]) - float(location[0])) < 0.000001
                    and abs(float(depot[1]) - float(location[1])) < 0.000001
                ):
                    return index
            logger.warning(f"⚠️ Депо {location} не намерено в PyVRP матрицата, използвам индекс {fallback_index}")
        return fallback_index

    def _matrix_location_coords(self) -> List[Tuple[float, float]]:
        location_coords = []
        num_locations = len(self.distance_matrix.distances)
        for loc_idx in range(num_locations):
            if loc_idx < len(self.unique_depots):
                coords = self.unique_depots[loc_idx]
            else:
                client_idx = loc_idx - len(self.unique_depots)
                if client_idx < len(self.customers):
                    coords = self.customers[client_idx].coordinates or (0, 0)
                else:
                    coords = (0, 0)
            location_coords.append(coords)
        return location_coords

    def _travel_time_seconds(
        self,
        from_node: int,
        to_node: int,
        location_coords: List[Tuple[float, float]],
    ) -> float:
        travel_time = float(self.distance_matrix.durations[from_node][to_node])
        if from_node < len(location_coords) and to_node < len(location_coords):
            traffic_multiplier = get_traffic_multiplier(
                self.location_config,
                location_coords[from_node],
                location_coords[to_node],
            )
            if traffic_multiplier > 1.0:
                travel_time *= traffic_multiplier
        return max(0.0, travel_time)

    def _format_schedule_time(self, total_seconds: float) -> str:
        total_minutes = int(round(total_seconds / 60))
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours:02d}:{minutes:02d}"

    def _format_customer_time_window_for_schedule(self, customer: Customer) -> str:
        return self._format_customer_time_windows_for_schedule(customer)

    def _build_route_schedule_entries(
        self,
        customers: List[Customer],
        depot_location: Tuple[float, float],
        vehicle_config: Optional[VehicleConfig] = None,
        end_location: Optional[Tuple[float, float]] = None,
        start_time_seconds: Optional[float] = None,
    ) -> Tuple[List[Dict[str, object]], float, float]:
        if not customers:
            return [], 0.0, 0.0

        depot_index = self._get_depot_index_for_location(depot_location, 0)
        end_depot_index = self._get_depot_index_for_location(end_location, depot_index)
        default_service_time_seconds = (
            float(vehicle_config.service_time_minutes) * 60
            if vehicle_config
            else 15 * 60
        )
        effective_start_seconds = (
            float(start_time_seconds)
            if start_time_seconds is not None
            else float(self._vehicle_start_seconds(vehicle_config))
        )
        current_clock_seconds = effective_start_seconds
        total_time_seconds = 0.0
        cumulative_distance_m = 0.0
        current_node = depot_index
        previous_stop_name = "Депо"
        entries: List[Dict[str, object]] = []
        location_coords = self._matrix_location_coords()

        for stop_index, customer in enumerate(customers, start=1):
            try:
                customer_matrix_idx = len(self.unique_depots) + self.customers.index(customer)
            except ValueError:
                logger.warning("Customer %s was not found while building PyVRP route schedule", customer.id)
                continue

            distance_m = max(
                0.0,
                float(self.distance_matrix.distances[current_node][customer_matrix_idx]),
            )
            travel_time_seconds = self._travel_time_seconds(current_node, customer_matrix_idx, location_coords)
            service_override = getattr(customer, "service_time_minutes", None)
            if service_override is None or (
                isinstance(service_override, str) and not service_override.strip()
            ):
                service_time_seconds = default_service_time_seconds
            else:
                try:
                    service_time_seconds = max(0.0, float(service_override) * 60)
                except (TypeError, ValueError, OverflowError):
                    logger.warning(
                        "Invalid service_time_minutes=%r for customer %s; using vehicle default.",
                        service_override,
                        customer.id,
                    )
                    service_time_seconds = default_service_time_seconds
            cumulative_distance_m += distance_m

            raw_arrival_seconds = current_clock_seconds + travel_time_seconds
            wait_seconds, time_window_status = self._time_window_status_for_arrival(
                raw_arrival_seconds,
                customer,
            )
            arrival_after_wait_seconds = raw_arrival_seconds + wait_seconds

            step_time_seconds = travel_time_seconds + wait_seconds + service_time_seconds
            total_time_seconds += step_time_seconds
            current_clock_seconds = arrival_after_wait_seconds + service_time_seconds

            entries.append({
                "customer": customer,
                "index": stop_index,
                "previous_stop_name": previous_stop_name,
                "distance_from_previous": distance_m / 1000,
                "cumulative_distance": cumulative_distance_m / 1000,
                "travel_time_minutes": travel_time_seconds / 60,
                "service_time_minutes": service_time_seconds / 60,
                "wait_minutes": wait_seconds / 60,
                "total_time_for_step": step_time_seconds / 60,
                "cumulative_time": total_time_seconds / 60,
                "start_time_minutes": effective_start_seconds / 60,
                "arrival_time_minutes": arrival_after_wait_seconds / 60,
                "total_time_with_start": current_clock_seconds / 60,
                "time_window_text": self._format_customer_time_window_for_schedule(customer),
                "time_window_status": time_window_status,
            })

            current_node = customer_matrix_idx
            previous_stop_name = customer.name

        if entries:
            cumulative_distance_m += max(
                0.0,
                float(self.distance_matrix.distances[current_node][end_depot_index]),
            )
            total_time_seconds += self._travel_time_seconds(
                current_node,
                end_depot_index,
                location_coords,
            )

        return entries, cumulative_distance_m, total_time_seconds

    def _calculate_route_metrics(
        self,
        customers: List[Customer],
        depot_location: Tuple[float, float],
        vehicle_config: Optional[VehicleConfig] = None,
        end_location: Optional[Tuple[float, float]] = None,
    ) -> Tuple[float, float]:
        """
        Изчислява разстояние и време за маршрут.

        Args:
            customers: Списък с клиенти в маршрута
            depot_location: GPS координати на стартовото депо
            end_location: GPS координати на крайната точка. Ако е None, използва стартовото депо.

        Returns:
            Кортеж (разстояние_м, време_сек)
        """
        _, total_distance, total_time = self._build_route_schedule_entries(
            customers,
            depot_location,
            vehicle_config,
            end_location,
        )
        return total_distance, total_time

        if not customers:
            return 0.0, 0.0

        total_distance = 0.0
        total_time = 0.0

        depot_index = self._get_depot_index_for_location(depot_location, 0)
        end_depot_index = self._get_depot_index_for_location(end_location, depot_index)

        # От депо до първия клиент
        current_node = depot_index
        
        if vehicle_config:
            service_time_s = vehicle_config.service_time_minutes * 60
        else:
            service_time_s = 15 * 60
        current_clock_s = self._vehicle_start_seconds(vehicle_config)

        # === ГРАДСКИ ТРАФИК: координати за всички локации и активни зони ===
        num_locations = len(self.distance_matrix.distances)
        location_coords = []
        for loc_idx in range(num_locations):
            if loc_idx < len(self.unique_depots):
                coords = self.unique_depots[loc_idx]
            else:
                client_idx = loc_idx - len(self.unique_depots)
                if client_idx < len(self.customers):
                    coords = self.customers[client_idx].coordinates or (0, 0)
                else:
                    coords = (0, 0)
            location_coords.append(coords)

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
            if current_node < len(location_coords) and customer_matrix_idx < len(location_coords):
                traffic_multiplier = get_traffic_multiplier(
                    self.location_config,
                    location_coords[current_node],
                    location_coords[customer_matrix_idx],
                )
                if traffic_multiplier > 1.0:
                    travel_time = travel_time * traffic_multiplier
            total_time += travel_time
            current_clock_s += travel_time

            wait_time, time_window_status = self._time_window_status_for_arrival(current_clock_s, customer)
            if wait_time:
                total_time += wait_time
                current_clock_s += wait_time
            elif time_window_status.startswith("След работно време"):
                logger.debug(
                    "Клиент %s е след работното време при PyVRP метрики: %.1f мин",
                    customer.id,
                    current_clock_s / 60,
                )

            total_time += service_time_s  # vehicle-specific service time
            current_clock_s += service_time_s

            current_node = customer_matrix_idx

        # От последния клиент до крайната точка
        total_distance += self.distance_matrix.distances[current_node][end_depot_index]
        travel_time_back = self.distance_matrix.durations[current_node][end_depot_index]
        if current_node < len(location_coords) and end_depot_index < len(location_coords):
            traffic_multiplier = get_traffic_multiplier(
                self.location_config,
                location_coords[current_node],
                location_coords[end_depot_index],
            )
            if traffic_multiplier > 1.0:
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

    def _get_vehicle_config_for_type_index(self, vehicle_type_index) -> Optional[VehicleConfig]:
        if not isinstance(vehicle_type_index, int):
            return None
        enabled_vehicle_configs = [v_config for v_config in self.vehicle_configs if v_config.enabled]
        if 0 <= vehicle_type_index < len(enabled_vehicle_configs):
            return enabled_vehicle_configs[vehicle_type_index]
        return None

    def _get_vehicle_display_name(self, vehicle_config: Optional[VehicleConfig], occurrence: int = 1) -> str:
        if not vehicle_config:
            return ""
        name = str(getattr(vehicle_config, "name", "") or "").strip()
        if name and int(getattr(vehicle_config, "count", 1) or 1) > 1:
            return f"{name} {max(1, occurrence)}"
        return name

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

    def __init__(
        self,
        config: CVRPConfig = None,
        location_config: LocationConfig = None,
        vehicle_configs: Optional[List[VehicleConfig]] = None,
    ):
        """
        Инициализира wrapper-а.

        Args:
            config: CVRPConfig (опционално)
        """
        self.config = config
        self.location_config = location_config
        self.vehicle_configs = vehicle_configs

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

        enabled_vehicles = self.vehicle_configs if self.vehicle_configs is not None else (full_config.vehicles or [])
        location_config = self.location_config or full_config.locations

        sorted_depots = build_ordered_depots(
            depot_location,
            enabled_vehicles,
            include_reload_locations=bool(getattr(self.config, "enable_multiple_trips", False)),
        )
        logger.info(f"Ред на депата в PyVRP solver: {sorted_depots}")

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
    vehicle_configs: Optional[List[VehicleConfig]] = None,
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
    solver = PyVRPSolverWrapper(config, location_config, vehicle_configs)
    return solver.solve(allocation, depot_location, distance_matrix)
