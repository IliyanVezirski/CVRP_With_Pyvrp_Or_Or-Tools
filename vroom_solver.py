"""VROOM adapter for the application's CVRP data model.

The module deliberately contains no VROOM installation or process-management
logic.  ``vroom_runtime.run_vroom_problem`` owns that boundary; this adapter
only builds the official JSON input and converts the JSON result back to the
shared :class:`CVRPSolution` representation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import (
    CVRPConfig,
    LocationConfig,
    VehicleConfig,
    build_ordered_depots,
    calculate_customer_drop_penalties,
    center_zone_cost_adjustment,
    get_traffic_multiplier,
)
from cvrp_solver import CVRPSolution, Route
from input_handler import Customer, is_mandatory_customer
from osrm_client import DistanceMatrix
from pyvrp_solver import PyVRPSolver
from warehouse_manager import WarehouseAllocation


logger = logging.getLogger(__name__)

_UINT32_MAX = (1 << 32) - 1
_UNBOUNDED_TIME_END = (1 << 31) - 1
_VOLUME_SCALE = 100


class VROOMSolverError(RuntimeError):
    """Raised when VROOM input, execution, or output is invalid."""


class VROOMUnsupportedFeatureError(VROOMSolverError):
    """Raised when the requested problem cannot be represented faithfully."""


def run_vroom_problem(
    payload: Dict[str, Any],
    config: CVRPConfig,
) -> Tuple[Dict[str, Any], str]:
    """Call the isolated VROOM runtime.

    The import is lazy so importing/testing the adapter never requires an
    installed VROOM binary.  Tests can also patch this single boundary.
    """

    try:
        from vroom_runtime import run_vroom_problem as runtime_call
    except ImportError as exc:  # pragma: no cover - integration boundary
        raise VROOMSolverError(
            "VROOM runtime is not available. Configure/install the isolated "
            "VROOM worker before selecting solver_type='vroom'."
        ) from exc

    return runtime_call(payload, config)


@dataclass(frozen=True)
class _VehicleRuntimeInfo:
    vroom_id: int
    config: VehicleConfig
    enabled_config_index: int
    occurrence: int
    physical_vehicle_id: int
    profile: str
    type_name: str
    vehicle_key: str
    vehicle_name: str
    start_location: Tuple[float, float]
    end_location: Tuple[float, float]


class VROOMSolver(PyVRPSolver):
    """Builds and extracts a single-trip VROOM CVRP problem."""

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
        super().__init__(
            config,
            vehicle_configs,
            customers,
            distance_matrix,
            unique_depots,
            center_zone_customers,
            location_config,
        )
        self._job_by_id: Dict[int, Customer] = {}
        self._vehicle_by_id: Dict[int, _VehicleRuntimeInfo] = {}

    def solve(self) -> CVRPSolution:
        """Build, run, extract, and independently validate a VROOM solution."""

        if bool(getattr(self.config, "enable_multiple_trips", False)):
            raise VROOMUnsupportedFeatureError(
                "VROOM is currently available only in single-trip mode. "
                "Disable 'Втори курсове / enable_multiple_trips' or choose "
                "PyVRP/OR-Tools; silently losing reload trips is not allowed."
            )

        payload = self.build_problem()
        response, version = run_vroom_problem(payload, self.config)
        return self._extract_solution(response, version)

    def build_problem(self) -> Dict[str, Any]:
        """Return an official VROOM JSON problem for the current input."""

        if not self.customers:
            raise VROOMSolverError("Cannot build a VROOM problem without customers.")
        if not self.unique_depots:
            raise VROOMSolverError("Cannot build a VROOM problem without a depot.")

        enabled_configs = [
            vehicle
            for vehicle in self.vehicle_configs
            if bool(getattr(vehicle, "enabled", False))
            and self._positive_int(getattr(vehicle, "count", 0), "vehicle count") > 0
        ]
        if not enabled_configs:
            raise VROOMSolverError("Cannot build a VROOM problem without active vehicles.")

        matrix_size = len(self.unique_depots) + len(self.customers)
        self._validate_source_matrices(matrix_size)
        matrix_coords = self._matrix_coordinates(matrix_size)

        type_names = {
            enabled_index: f"vroom_type_{enabled_index + 1}"
            for enabled_index in range(len(enabled_configs))
        }
        profiles = {
            enabled_index: f"vroom_profile_{enabled_index + 1}"
            for enabled_index in range(len(enabled_configs))
        }

        service_per_type = {
            type_names[index]: self._minutes_to_seconds(
                getattr(vehicle, "service_time_minutes", 0),
                f"service time for vehicle config {index + 1}",
            )
            for index, vehicle in enumerate(enabled_configs)
        }

        priorities = self._customer_priorities()
        jobs: List[Dict[str, Any]] = []
        self._job_by_id = {}
        for customer_index, customer in enumerate(self.customers):
            job_id = customer_index + 1
            self._job_by_id[job_id] = customer
            job: Dict[str, Any] = {
                "id": job_id,
                "description": str(getattr(customer, "name", "") or getattr(customer, "id", "")),
                "location_index": len(self.unique_depots) + customer_index,
                "delivery": [
                    self._scaled_volume(getattr(customer, "volume", 0), f"customer {job_id} volume"),
                    1,
                ],
                "priority": priorities[customer_index],
            }

            location = self._optional_coordinates(getattr(customer, "coordinates", None))
            if location is not None:
                job["location"] = self._vroom_coordinates(location)

            override = self._customer_service_override(customer, job_id)
            if override is None:
                job["service"] = 0
                job["service_per_type"] = dict(service_per_type)
            else:
                job["service"] = override

            windows = self._customer_time_windows_seconds(customer)
            if windows:
                validated_windows = []
                for window_index, (start, end) in enumerate(windows, start=1):
                    start_value = self._uint32(
                        start,
                        f"start of time window {window_index} for customer job {job_id}",
                    )
                    end_value = self._uint32(
                        end,
                        f"end of time window {window_index} for customer job {job_id}",
                    )
                    if end_value < start_value:
                        raise VROOMSolverError(
                            f"Invalid time window {window_index} for customer job {job_id}: "
                            f"end {end_value} is before start {start_value}."
                        )
                    validated_windows.append([start_value, end_value])
                job["time_windows"] = validated_windows

            jobs.append(job)

        matrices = {
            profiles[index]: self._profile_matrices(vehicle, matrix_coords)
            for index, vehicle in enumerate(enabled_configs)
        }

        vehicles: List[Dict[str, Any]] = []
        self._vehicle_by_id = {}
        next_vroom_id = 1
        physical_vehicle_id = 0
        for enabled_index, vehicle_config in enumerate(enabled_configs):
            count = self._positive_int(
                getattr(vehicle_config, "count", 0),
                f"count for vehicle config {enabled_index + 1}",
            )
            for occurrence in range(1, count + 1):
                runtime_info, vehicle = self._build_vehicle(
                    vehicle_config=vehicle_config,
                    enabled_config_index=enabled_index,
                    occurrence=occurrence,
                    physical_vehicle_id=physical_vehicle_id,
                    vroom_id=next_vroom_id,
                    profile=profiles[enabled_index],
                    type_name=type_names[enabled_index],
                )
                vehicles.append(vehicle)
                self._vehicle_by_id[next_vroom_id] = runtime_info
                next_vroom_id += 1
                physical_vehicle_id += 1

        return {
            "jobs": jobs,
            "vehicles": vehicles,
            "matrices": matrices,
        }

    def _build_vehicle(
        self,
        *,
        vehicle_config: VehicleConfig,
        enabled_config_index: int,
        occurrence: int,
        physical_vehicle_id: int,
        vroom_id: int,
        profile: str,
        type_name: str,
    ) -> Tuple[_VehicleRuntimeInfo, Dict[str, Any]]:
        start_location = self._required_coordinates(
            getattr(vehicle_config, "start_location", None) or self.unique_depots[0],
            f"start location for vehicle {vroom_id}",
        )
        end_location = self._required_coordinates(
            getattr(vehicle_config, "end_location", None) or start_location,
            f"end location for vehicle {vroom_id}",
        )
        start_index = self._exact_depot_index(start_location, f"start location for vehicle {vroom_id}")
        end_index = self._exact_depot_index(end_location, f"end location for vehicle {vroom_id}")

        daily_limit = getattr(vehicle_config, "max_customers_per_day", None)
        if daily_limit is None:
            daily_limit = getattr(vehicle_config, "max_customers_per_route", None)
        if daily_limit is None:
            task_limit = len(self.customers)
        else:
            task_limit = self._nonnegative_int(daily_limit, f"daily customer limit for vehicle {vroom_id}")

        capacity = self._scaled_volume(
            getattr(vehicle_config, "capacity", 0),
            f"capacity for vehicle {vroom_id}",
        )
        start_seconds = self._uint32(
            self._vehicle_start_seconds(vehicle_config),
            f"start time for vehicle {vroom_id}",
        )

        max_time_hours = self._finite_nonnegative(
            getattr(vehicle_config, "max_time_hours", 0),
            f"max time for vehicle {vroom_id}",
        )
        if max_time_hours > 0:
            max_time_seconds = self._uint32(
                max_time_hours * 3600,
                f"max time for vehicle {vroom_id}",
            )
            time_window_end = self._uint32(
                start_seconds + max_time_seconds,
                f"time-window end for vehicle {vroom_id}",
            )
        else:
            max_time_seconds = None
            time_window_end = _UNBOUNDED_TIME_END

        fixed_cost = self._finite_nonnegative(
            getattr(vehicle_config, "fixed_cost", 0),
            f"fixed cost for vehicle {vroom_id}",
        )
        if self._objective_metric() == "time":
            fixed_cost_value = self._uint32(
                self._objective_penalty_cost(fixed_cost),
                f"converted fixed cost for vehicle {vroom_id}",
            )
        else:
            fixed_cost_value = self._uint32(fixed_cost, f"fixed cost for vehicle {vroom_id}")

        costs: Dict[str, int] = {"fixed": fixed_cost_value}
        if self._objective_metric() == "time":
            # VROOM custom costs price travel. This official vehicle cost field
            # additionally prices the actual per-client service duration.
            costs["per_task_hour"] = 3600

        vehicle: Dict[str, Any] = {
            "id": vroom_id,
            "description": self._get_vehicle_display_name(vehicle_config, occurrence)
            or str(getattr(vehicle_config.vehicle_type, "value", vehicle_config.vehicle_type)),
            "profile": profile,
            "type": type_name,
            "start": self._vroom_coordinates(start_location),
            "start_index": start_index,
            "end": self._vroom_coordinates(end_location),
            "end_index": end_index,
            "capacity": [capacity, task_limit],
            "costs": costs,
            "time_window": [start_seconds, time_window_end],
            "max_tasks": task_limit,
        }
        if max_time_seconds is not None:
            vehicle["max_travel_time"] = max_time_seconds

        max_distance_km = getattr(vehicle_config, "max_distance_km", None)
        if max_distance_km is not None:
            max_distance = self._finite_nonnegative(
                max_distance_km,
                f"max distance for vehicle {vroom_id}",
            )
            vehicle["max_distance"] = self._uint32(
                max_distance * 1000,
                f"max distance for vehicle {vroom_id}",
            )

        vehicle_key = self._vehicle_key(vehicle_config, enabled_config_index, occurrence)
        runtime_info = _VehicleRuntimeInfo(
            vroom_id=vroom_id,
            config=vehicle_config,
            enabled_config_index=enabled_config_index,
            occurrence=occurrence,
            physical_vehicle_id=physical_vehicle_id,
            profile=profile,
            type_name=type_name,
            vehicle_key=vehicle_key,
            vehicle_name=self._get_vehicle_display_name(vehicle_config, occurrence),
            start_location=start_location,
            end_location=end_location,
        )
        return runtime_info, vehicle

    def _profile_matrices(
        self,
        vehicle_config: VehicleConfig,
        matrix_coords: Sequence[Optional[Tuple[float, float]]],
    ) -> Dict[str, List[List[int]]]:
        size = len(matrix_coords)
        durations: List[List[int]] = []
        distances: List[List[int]] = []
        costs: List[List[int]] = []
        objective_is_time = self._objective_metric() == "time"

        for from_index in range(size):
            duration_row: List[int] = []
            distance_row: List[int] = []
            cost_row: List[int] = []
            for to_index in range(size):
                distance = self._uint32(
                    self.distance_matrix.distances[from_index][to_index],
                    f"distance matrix[{from_index}][{to_index}]",
                )
                raw_duration = self._finite_nonnegative(
                    self.distance_matrix.durations[from_index][to_index],
                    f"duration matrix[{from_index}][{to_index}]",
                )
                traffic_multiplier = get_traffic_multiplier(
                    self.location_config,
                    matrix_coords[from_index],
                    matrix_coords[to_index],
                )
                adjusted_duration = self._uint32(
                    self._canonical_travel_seconds(
                        raw_duration,
                        traffic_multiplier,
                    ),
                    f"traffic-adjusted duration matrix[{from_index}][{to_index}]",
                )

                base_cost = adjusted_duration if objective_is_time else distance
                objective_cost = base_cost
                if to_index >= len(self.unique_depots):
                    multiplier, penalty = center_zone_cost_adjustment(
                        matrix_coords[to_index],
                        vehicle_config.vehicle_type,
                        self.location_config,
                        self._objective_penalty_cost,
                    )
                    objective_cost = int(round(base_cost * float(multiplier))) + int(penalty)

                duration_row.append(adjusted_duration)
                distance_row.append(distance)
                cost_row.append(
                    self._uint32(
                        objective_cost,
                        f"cost matrix[{from_index}][{to_index}]",
                    )
                )

            durations.append(duration_row)
            distances.append(distance_row)
            costs.append(cost_row)

        return {
            "durations": durations,
            "distances": distances,
            "costs": costs,
        }

    def _extract_solution(self, response: Dict[str, Any], version: Any) -> CVRPSolution:
        if not isinstance(response, dict):
            raise VROOMSolverError("VROOM returned a non-object JSON response.")

        try:
            code = int(response.get("code", 0))
        except (TypeError, ValueError) as exc:
            raise VROOMSolverError("VROOM returned an invalid status code.") from exc
        if code != 0:
            error = str(response.get("error", "unknown VROOM error") or "unknown VROOM error")
            raise VROOMSolverError(f"VROOM failed with code {code}: {error}")

        routes: List[Route] = []
        assigned_job_ids: set[int] = set()
        used_vehicle_ids: set[int] = set()
        total_distance_km = 0.0
        total_time_minutes = 0.0
        total_time_seconds = 0.0
        total_volume = 0.0
        response_has_violations = False

        response_routes = response.get("routes", []) or []
        if not isinstance(response_routes, list):
            raise VROOMSolverError("VROOM response field 'routes' must be an array.")

        for response_route in response_routes:
            if not isinstance(response_route, dict):
                raise VROOMSolverError("VROOM returned an invalid route object.")
            vehicle_id = self._response_integer(response_route.get("vehicle"), "route vehicle id")
            runtime_info = self._vehicle_by_id.get(vehicle_id)
            if runtime_info is None:
                raise VROOMSolverError(f"VROOM returned unknown vehicle id {vehicle_id}.")
            if vehicle_id in used_vehicle_ids:
                raise VROOMSolverError(f"VROOM returned more than one route for vehicle id {vehicle_id}.")
            used_vehicle_ids.add(vehicle_id)

            route_customers: List[Customer] = []
            steps = response_route.get("steps", []) or []
            if not isinstance(steps, list):
                raise VROOMSolverError(f"VROOM route for vehicle {vehicle_id} has invalid steps.")
            for step in steps:
                if not isinstance(step, dict) or step.get("type") != "job":
                    continue
                raw_job_id = step.get("id", step.get("job"))
                job_id = self._response_integer(raw_job_id, "job id")
                customer = self._job_by_id.get(job_id)
                if customer is None:
                    raise VROOMSolverError(f"VROOM returned unknown job id {job_id}.")
                if job_id in assigned_job_ids:
                    raise VROOMSolverError(f"VROOM assigned job id {job_id} more than once.")
                assigned_job_ids.add(job_id)
                route_customers.append(customer)

            if not route_customers:
                continue

            start_seconds = float(self._vehicle_start_seconds(runtime_info.config))
            schedule_entries, route_distance_m, route_time_s = self._build_route_schedule_entries(
                route_customers,
                runtime_info.start_location,
                runtime_info.config,
                runtime_info.end_location,
                start_time_seconds=start_seconds,
            )
            route_volume = sum(
                max(0.0, float(getattr(customer, "volume", 0) or 0))
                for customer in route_customers
            )
            route_violations = response_route.get("violations", []) or []
            route_is_feasible = not bool(route_violations)
            response_has_violations = response_has_violations or not route_is_feasible

            route = Route(
                vehicle_type=runtime_info.config.vehicle_type,
                vehicle_id=runtime_info.physical_vehicle_id,
                customers=route_customers,
                depot_location=runtime_info.start_location,
                end_location=runtime_info.end_location,
                vehicle_name=runtime_info.vehicle_name,
                total_distance_km=route_distance_m / 1000,
                total_time_minutes=route_time_s / 60,
                total_volume=route_volume,
                is_feasible=route_is_feasible,
                schedule_entries=schedule_entries,
                vehicle_key=runtime_info.vehicle_key,
                trip_number=1,
                trip_count=1,
                route_id=f"{runtime_info.vehicle_key}:trip:1",
                planned_start_minutes=start_seconds / 60,
                planned_end_minutes=(start_seconds + route_time_s) / 60,
            )
            routes.append(route)
            total_distance_km += route.total_distance_km
            total_time_minutes += route.total_time_minutes
            total_time_seconds += route_time_s
            total_volume += route_volume

        explicit_unassigned = response.get("unassigned", []) or []
        if not isinstance(explicit_unassigned, list):
            raise VROOMSolverError("VROOM response field 'unassigned' must be an array.")
        for item in explicit_unassigned:
            if not isinstance(item, dict):
                raise VROOMSolverError("VROOM returned an invalid unassigned task object.")
            job_id = self._response_integer(item.get("id", item.get("job")), "unassigned job id")
            if job_id not in self._job_by_id:
                raise VROOMSolverError(f"VROOM returned unknown unassigned job id {job_id}.")
            if job_id in assigned_job_ids:
                raise VROOMSolverError(f"VROOM returned job id {job_id} as both assigned and unassigned.")

        dropped_customers = [
            customer
            for job_id, customer in self._job_by_id.items()
            if job_id not in assigned_job_ids
        ]

        summary = response.get("summary", {}) or {}
        if not isinstance(summary, dict):
            raise VROOMSolverError("VROOM response field 'summary' must be an object.")

        if self._objective_metric() == "time":
            fitness_score = float(total_time_seconds)
        else:
            raw_cost = summary.get(
                "cost",
                sum(float(route_data.get("cost", 0) or 0) for route_data in response_routes),
            )
            try:
                fitness_score = float(raw_cost)
            except (TypeError, ValueError, OverflowError) as exc:
                raise VROOMSolverError("VROOM returned an invalid solution cost.") from exc
            if not math.isfinite(fitness_score) or fitness_score < 0:
                raise VROOMSolverError("VROOM returned a non-finite or negative solution cost.")

        solution = CVRPSolution(
            routes=routes,
            dropped_customers=dropped_customers,
            total_distance_km=total_distance_km,
            total_time_minutes=total_time_minutes,
            total_vehicles_used=len(routes),
            fitness_score=fitness_score,
            is_feasible=bool(routes) and not response_has_violations,
            total_served_volume=total_volume,
            total_trips=len(routes),
            second_trips_count=0,
            solver_requested="vroom",
            solver_used="vroom",
            solver_backend="vroom",
            solver_version=str(version or "unknown"),
            solver_fallback_used=False,
            solver_fallback_reason="",
        )

        if not routes:
            solution.is_feasible = False
            solution.fitness_score = float("inf")

        violations = self._validate_extracted_solution_constraints(solution)
        if violations:
            logger.error(
                "VROOM result failed the independent hard-constraint audit: %s",
                "; ".join(violations),
            )
            for route in solution.routes:
                route.is_feasible = False
            solution.is_feasible = False
            solution.fitness_score = float("inf")

        return solution

    def _customer_priorities(self) -> List[int]:
        if not bool(getattr(self.config, "allow_customer_skipping", True)):
            return [100 for _ in self.customers]

        penalties = calculate_customer_drop_penalties(
            self.customers,
            self.unique_depots,
            self.config,
        )
        if len(penalties) != len(self.customers):
            raise VROOMSolverError(
                "Customer drop-penalty calculation returned a different number of values than jobs."
            )
        if not penalties:
            return []
        minimum = min(penalties)
        maximum = max(penalties)
        if maximum == minimum:
            priorities = [100 for _ in penalties]
        else:
            priorities = [
            max(1, min(100, 1 + int(round(99 * (penalty - minimum) / (maximum - minimum)))))
            for penalty in penalties
            ]

        # VROOM has no hard-required job flag. Priority 100 gives mandatory
        # jobs strict preference; the independent audit below rejects any
        # response that still leaves one unassigned as infeasible.
        if any(is_mandatory_customer(customer) for customer in self.customers):
            priorities = [
                100 if is_mandatory_customer(customer) else min(priority, 99)
                for customer, priority in zip(self.customers, priorities)
            ]
        return priorities

    def _customer_service_override(self, customer: Customer, job_id: int) -> Optional[int]:
        value = getattr(customer, "service_time_minutes", None)
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return self._minutes_to_seconds(value, f"service time for customer job {job_id}")

    def _matrix_coordinates(self, expected_size: int) -> List[Optional[Tuple[float, float]]]:
        coordinates: List[Optional[Tuple[float, float]]] = [
            self._required_coordinates(depot, f"depot {index}")
            for index, depot in enumerate(self.unique_depots)
        ]
        coordinates.extend(
            self._optional_coordinates(getattr(customer, "coordinates", None))
            for customer in self.customers
        )
        if len(coordinates) != expected_size:
            raise VROOMSolverError(
                f"Internal VROOM location order has {len(coordinates)} entries; expected {expected_size}."
            )
        return coordinates

    def _validate_source_matrices(self, expected_size: int) -> None:
        for matrix_name in ("distances", "durations"):
            matrix = getattr(self.distance_matrix, matrix_name, None)
            if not isinstance(matrix, Sequence) or isinstance(matrix, (str, bytes)):
                raise VROOMSolverError(f"OSRM {matrix_name} matrix is missing or invalid.")
            if len(matrix) != expected_size:
                raise VROOMSolverError(
                    f"OSRM {matrix_name} matrix has {len(matrix)} rows; expected {expected_size}."
                )
            for row_index, row in enumerate(matrix):
                if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
                    raise VROOMSolverError(f"OSRM {matrix_name} row {row_index} is invalid.")
                if len(row) != expected_size:
                    raise VROOMSolverError(
                        f"OSRM {matrix_name} row {row_index} has {len(row)} columns; "
                        f"expected {expected_size}."
                    )
                for column_index, value in enumerate(row):
                    self._uint32(value, f"{matrix_name}[{row_index}][{column_index}]")

    def _exact_depot_index(self, location: Tuple[float, float], label: str) -> int:
        for index, depot in enumerate(self.unique_depots):
            if (
                abs(float(depot[0]) - float(location[0])) < 0.000001
                and abs(float(depot[1]) - float(location[1])) < 0.000001
            ):
                return index
        raise VROOMSolverError(
            f"{label} {location!r} is not present in the custom matrix depot order."
        )

    @staticmethod
    def _response_integer(value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise VROOMSolverError(f"VROOM returned an invalid {label}: {value!r}.")
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise VROOMSolverError(f"VROOM returned an invalid {label}: {value!r}.") from exc
        return result

    @staticmethod
    def _finite_nonnegative(value: Any, label: str) -> float:
        if isinstance(value, bool):
            numeric = float(int(value))
        else:
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise VROOMSolverError(f"Invalid {label}: {value!r}.") from exc
        if not math.isfinite(numeric) or numeric < 0:
            raise VROOMSolverError(f"Invalid {label}: expected a finite non-negative value, got {value!r}.")
        return numeric

    @classmethod
    def _uint32(cls, value: Any, label: str) -> int:
        numeric = cls._finite_nonnegative(value, label)
        rounded = int(round(numeric))
        if rounded > _UINT32_MAX:
            raise VROOMSolverError(f"Invalid {label}: {rounded} exceeds VROOM uint32 range.")
        return rounded

    @classmethod
    def _minutes_to_seconds(cls, value: Any, label: str) -> int:
        numeric = cls._finite_nonnegative(value, label)
        return cls._uint32(cls._canonical_service_seconds(numeric), label)

    @classmethod
    def _scaled_volume(cls, value: Any, label: str) -> int:
        numeric = cls._finite_nonnegative(value, label)
        return cls._uint32(numeric * _VOLUME_SCALE, label)

    @classmethod
    def _nonnegative_int(cls, value: Any, label: str) -> int:
        numeric = cls._finite_nonnegative(value, label)
        if not numeric.is_integer():
            raise VROOMSolverError(f"Invalid {label}: expected an integer, got {value!r}.")
        return cls._uint32(numeric, label)

    @classmethod
    def _positive_int(cls, value: Any, label: str) -> int:
        result = cls._nonnegative_int(value, label)
        if result <= 0:
            return 0
        return result

    @classmethod
    def _optional_coordinates(cls, value: Any) -> Optional[Tuple[float, float]]:
        if value is None:
            return None
        try:
            if len(value) != 2:
                return None
            latitude = float(value[0])
            longitude = float(value[1])
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            return None
        return latitude, longitude

    @classmethod
    def _required_coordinates(cls, value: Any, label: str) -> Tuple[float, float]:
        coordinates = cls._optional_coordinates(value)
        if coordinates is None:
            raise VROOMSolverError(f"Invalid {label}: expected finite (latitude, longitude) coordinates.")
        return coordinates

    @staticmethod
    def _vroom_coordinates(location: Tuple[float, float]) -> List[float]:
        # Application coordinates are (lat, lon); VROOM requires [lon, lat].
        return [float(location[1]), float(location[0])]


def solve_cvrp_vroom(
    allocation: WarehouseAllocation,
    depot_location: Tuple[float, float],
    distance_matrix: DistanceMatrix,
    config: Optional[CVRPConfig] = None,
    location_config: Optional[LocationConfig] = None,
    vehicle_configs: Optional[List[VehicleConfig]] = None,
) -> CVRPSolution:
    """Solve the application's CVRP input through the isolated VROOM runtime."""

    from config import get_config

    full_config = get_config()
    solver_config = config or full_config.cvrp
    vehicles = vehicle_configs if vehicle_configs is not None else list(full_config.vehicles or [])
    locations = location_config or full_config.locations
    unique_depots = build_ordered_depots(depot_location, vehicles, include_reload_locations=False)

    solver = VROOMSolver(
        solver_config,
        vehicles,
        list(allocation.vehicle_customers or []),
        distance_matrix,
        unique_depots,
        list(allocation.center_zone_customers or []),
        locations,
    )
    return solver.solve()


__all__ = [
    "VROOMSolver",
    "VROOMSolverError",
    "VROOMUnsupportedFeatureError",
    "run_vroom_problem",
    "solve_cvrp_vroom",
]
