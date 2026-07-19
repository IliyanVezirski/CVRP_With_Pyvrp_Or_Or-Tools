"""Bizant adapter for the open-source ``reinterpretcat/vrp`` solver.

The module keeps its original prototype filename so existing local benchmark
tools continue to work.  The application-facing import is :mod:`vrp_solver`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import (
    CVRPConfig,
    LocationConfig,
    VehicleConfig,
    calculate_customer_drop_penalties,
    center_zone_cost_adjustment,
    get_traffic_multiplier,
)
from cvrp_solver import CVRPSolution, Route
from input_handler import Customer, is_mandatory_customer
from osrm_client import DistanceMatrix
from vroom_solver import VROOMSolver, VROOMSolverError
from vrp_runtime import run_vrp_problem, vrp_max_generations, vrp_thread_count


logger = logging.getLogger(__name__)

_BASE_INSTANT = datetime(2026, 7, 16, tzinfo=timezone.utc)
_VOLUME_SCALE = 100


class VRPRustPrototypeError(RuntimeError):
    """Raised when the adapter cannot model or parse the problem."""


@dataclass(frozen=True)
class _VehicleInfo:
    vehicle_id: str
    type_id: str
    config: VehicleConfig
    enabled_config_index: int
    occurrence: int
    physical_vehicle_id: int
    vehicle_key: str
    vehicle_name: str
    start_location: Tuple[float, float]
    end_location: Tuple[float, float]
    reload_location: Tuple[float, float]


@dataclass
class _TripSegment:
    customers: List[Customer]
    start_location: Tuple[float, float]
    end_location: Tuple[float, float]
    start_seconds: float


class VRPRustPrototype(VROOMSolver):
    """Translate the current Bizant problem to pragmatic VRP JSON and back."""

    def __init__(
        self,
        config: CVRPConfig,
        vehicle_configs: List[VehicleConfig],
        customers: List[Customer],
        distance_matrix: DistanceMatrix,
        unique_depots: List[Tuple[float, float]],
        center_zone_customers: Optional[List[Customer]] = None,
        location_config: Optional[LocationConfig] = None,
        *,
        time_limit_seconds: Optional[int] = None,
        thread_pools: Optional[int] = None,
        threads_per_pool: Optional[int] = None,
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
        self.time_limit_seconds = max(
            1,
            int(time_limit_seconds or getattr(config, "time_limit_seconds", 300) or 300),
        )
        if thread_pools is not None and threads_per_pool is not None:
            self.thread_pools = max(1, int(thread_pools))
            self.threads_per_pool = max(1, int(threads_per_pool))
        else:
            total_threads = vrp_thread_count(config)
            pools = min(4, total_threads)
            while pools > 1 and total_threads % pools:
                pools -= 1
            self.thread_pools = pools
            self.threads_per_pool = max(1, total_threads // pools)
        self._job_by_id: Dict[str, Customer] = {}
        self._vehicle_by_id: Dict[str, _VehicleInfo] = {}

    def solve(self) -> CVRPSolution:
        request = self.build_request()
        response, version = run_vrp_problem(request, self.config)
        return self._extract_solution(response, version)

    def build_request(self) -> Dict[str, Any]:
        if not self.customers:
            raise VRPRustPrototypeError("Cannot solve with VRP-Rust without customers.")
        if not self.unique_depots:
            raise VRPRustPrototypeError("Cannot solve with VRP-Rust without depots.")

        enabled_configs = [
            vehicle
            for vehicle in self.vehicle_configs
            if bool(getattr(vehicle, "enabled", False)) and int(getattr(vehicle, "count", 0) or 0) > 0
        ]
        if not enabled_configs:
            raise VRPRustPrototypeError("Cannot solve with VRP-Rust without active vehicles.")

        if self._objective_metric() == "time":
            has_zone_preferences = any(
                center_zone_cost_adjustment(
                    getattr(customer, "coordinates", None),
                    vehicle_config.vehicle_type,
                    self.location_config,
                    self._objective_penalty_cost,
                )
                != (1.0, 0)
                for vehicle_config in enabled_configs
                for customer in self.customers
            )
            if has_zone_preferences:
                logger.warning(
                    "VRP-Rust pure time mode minimizes complete route duration. "
                    "Vehicle-zone discounts remain outside the time fitness and cannot be "
                    "used as a vehicle-specific soft tie-break by the pragmatic format. "
                    "Use distance mode when zone assignment preferences must influence the solve."
                )

        matrix_size = len(self.unique_depots) + len(self.customers)
        self._validate_source_matrices(matrix_size)
        matrix_coords = self._matrix_coordinates(matrix_size)

        jobs: List[Dict[str, Any]] = []
        self._job_by_id = {}
        drop_values = calculate_customer_drop_penalties(
            self.customers,
            self.unique_depots,
            self.config,
        )
        allow_skipping = bool(getattr(self.config, "allow_customer_skipping", True))
        for customer_index, customer in enumerate(self.customers):
            job_id = f"customer_{customer_index + 1}"
            self._job_by_id[job_id] = customer
            place: Dict[str, Any] = {
                "location": {"index": len(self.unique_depots) + customer_index},
                # Service is profile-dependent and is therefore added to the
                # outgoing travel-time row for this customer.
                "duration": 0,
            }
            windows = self._customer_time_windows_seconds(customer)
            if windows:
                place["times"] = [
                    [self._iso_time(start), self._iso_time(end)]
                    for start, end in windows
                ]
            job: Dict[str, Any] = {
                    "id": job_id,
                    "deliveries": [
                        {
                            "places": [place],
                            "demand": [self._scaled_volume(getattr(customer, "volume", 0), job_id)],
                        }
                    ],
                }
            if (
                allow_skipping
                and not is_mandatory_customer(customer)
                and customer_index < len(drop_values)
            ):
                job["value"] = max(1, int(drop_values[customer_index]))
            jobs.append(job)

        profiles = []
        matrices = []
        vehicles = []
        self._vehicle_by_id = {}
        physical_vehicle_id = 0
        for enabled_index, vehicle_config in enumerate(enabled_configs):
            profile_name = f"bizant_profile_{enabled_index + 1}"
            type_id = f"bizant_type_{enabled_index + 1}"
            profiles.append({"name": profile_name})
            matrices.append(
                self._build_profile_matrix(
                    profile_name,
                    vehicle_config,
                    matrix_coords,
                )
            )

            vehicle_ids = []
            count = max(0, int(getattr(vehicle_config, "count", 0) or 0))
            start_location = self._required_coordinates(
                getattr(vehicle_config, "start_location", None) or self.unique_depots[0],
                f"start location for {type_id}",
            )
            end_location = self._required_coordinates(
                getattr(vehicle_config, "end_location", None) or start_location,
                f"end location for {type_id}",
            )
            reload_location = self._required_coordinates(
                getattr(vehicle_config, "reload_location", None) or start_location,
                f"reload location for {type_id}",
            )
            start_index = self._exact_depot_index(start_location, f"start location for {type_id}")
            end_index = self._exact_depot_index(end_location, f"end location for {type_id}")
            reload_index = self._exact_depot_index(reload_location, f"reload location for {type_id}")

            for occurrence in range(1, count + 1):
                vehicle_id = f"vehicle_{enabled_index + 1}_{occurrence}"
                vehicle_ids.append(vehicle_id)
                info = _VehicleInfo(
                    vehicle_id=vehicle_id,
                    type_id=type_id,
                    config=vehicle_config,
                    enabled_config_index=enabled_index,
                    occurrence=occurrence,
                    physical_vehicle_id=physical_vehicle_id,
                    vehicle_key=self._vehicle_key(vehicle_config, enabled_index, occurrence),
                    vehicle_name=self._get_vehicle_display_name(vehicle_config, occurrence),
                    start_location=start_location,
                    end_location=end_location,
                    reload_location=reload_location,
                )
                self._vehicle_by_id[vehicle_id] = info
                physical_vehicle_id += 1

            start_seconds = self._vehicle_start_seconds(vehicle_config)
            max_time_seconds = max(0, int(round(float(getattr(vehicle_config, "max_time_hours", 0) or 0) * 3600)))
            if max_time_seconds <= 0:
                max_time_seconds = 24 * 3600
            start_iso = self._iso_time(start_seconds)
            end_iso = self._iso_time(start_seconds + max_time_seconds)
            shift: Dict[str, Any] = {
                "start": {
                    "earliest": start_iso,
                    "latest": start_iso,
                    "location": {"index": start_index},
                },
                "end": {
                    "latest": end_iso,
                    "location": {"index": end_index},
                },
            }
            if bool(getattr(self.config, "enable_multiple_trips", False)):
                shift["reloads"] = [
                    {
                        "location": {"index": reload_index},
                        "duration": max(
                            0,
                            int(round(float(getattr(vehicle_config, "reload_time_minutes", 0) or 0) * 60)),
                        ),
                    }
                ]

            limits: Dict[str, Any] = {"maxDuration": max_time_seconds}
            daily_limit = getattr(vehicle_config, "max_customers_per_day", None)
            if daily_limit is None:
                daily_limit = getattr(vehicle_config, "max_customers_per_route", None)
            if daily_limit:
                if bool(getattr(self.config, "enable_multiple_trips", False)):
                    raise VRPRustPrototypeError(
                        "VRP-Rust tourSize also counts reload activities, so it cannot enforce "
                        "max_customers_per_day exactly together with multiple trips. Use PyVRP/"
                        "OR-Tools, disable multiple trips, or remove the daily customer limit."
                    )
                limits["tourSize"] = int(daily_limit)

            max_distance = getattr(vehicle_config, "max_distance_km", None)
            if max_distance:
                zone_cost_active = any(
                    center_zone_cost_adjustment(
                        getattr(customer, "coordinates", None),
                        vehicle_config.vehicle_type,
                        self.location_config,
                        self._objective_penalty_cost,
                    )
                    != (1.0, 0)
                    for customer in self.customers
                )
                if self._objective_metric() == "distance" and zone_cost_active:
                    raise VRPRustPrototypeError(
                        "VRP-Rust uses its distance channel for vehicle-zone costs in distance mode; "
                        "max_distance_km cannot be enforced simultaneously. Use PyVRP/OR-Tools, "
                        "remove the distance limit, or select the time objective."
                    )
                limits["maxDistance"] = max(
                    0,
                    int(round(float(max_distance) * 1000)),
                )

            vehicles.append(
                {
                    "typeId": type_id,
                    "vehicleIds": vehicle_ids,
                    "profile": {"matrix": profile_name},
                    "costs": {
                        "fixed": self._objective_penalty_cost(
                            getattr(vehicle_config, "fixed_cost", 0)
                        ),
                        "distance": 1 if self._objective_metric() == "distance" else 0,
                        "time": 0 if self._objective_metric() == "distance" else 1,
                    },
                    "shifts": [shift],
                    "capacity": [
                        self._scaled_volume(
                            getattr(vehicle_config, "capacity", 0),
                            f"capacity for {type_id}",
                        )
                    ],
                    "limits": limits,
                }
            )

        objectives: List[Dict[str, Any]] = []
        if allow_skipping and any(drop_values):
            objectives.append({"type": "maximize-value", "reductionFactor": 0.1})
        objectives.append({"type": "minimize-unassigned"})
        objectives.append(
            {
                "type": (
                    "minimize-duration"
                    if self._objective_metric() == "time"
                    else "minimize-cost"
                )
            }
        )
        problem = {
            "plan": {"jobs": jobs},
            "fleet": {"vehicles": vehicles, "profiles": profiles},
            "objectives": objectives,
        }
        log_progress = bool(getattr(self.config, "vrp_log_progress", True))
        solver_config = {
            "termination": {
                "maxTime": self.time_limit_seconds,
                "maxGenerations": vrp_max_generations(self.config),
            },
            "telemetry": {
                "progress": {
                    "enabled": log_progress,
                    "logBest": 100,
                    "logPopulation": 1000,
                    "dumpPopulation": False,
                }
            },
            "environment": {
                "parallelism": {
                    "numThreadPools": self.thread_pools,
                    "threadsPerPool": self.threads_per_pool,
                },
                "logging": {"enabled": log_progress, "prefix": "[VRP-Rust]"},
                "isExperimental": False,
            },
        }
        return {
            "problem": problem,
            "matrices": matrices,
            "solver_config": solver_config,
        }

    def _build_profile_matrix(
        self,
        profile_name: str,
        vehicle_config: VehicleConfig,
        matrix_coords: Sequence[Optional[Tuple[float, float]]],
    ) -> Dict[str, Any]:
        size = len(matrix_coords)
        travel_times: List[int] = []
        objective_distances: List[int] = []
        default_service_seconds = max(
            0,
            int(round(float(getattr(vehicle_config, "service_time_minutes", 0) or 0) * 60)),
        )

        service_by_source = [0 for _ in range(size)]
        for customer_index, customer in enumerate(self.customers):
            override = getattr(customer, "service_time_minutes", None)
            if override is None or (isinstance(override, str) and not override.strip()):
                service_seconds = default_service_seconds
            else:
                service_seconds = max(0, int(round(float(override) * 60)))
            service_by_source[len(self.unique_depots) + customer_index] = service_seconds

        for from_index in range(size):
            for to_index in range(size):
                # The pragmatic matrix contract requires a zero diagonal.
                # Service belongs to a real departure to another location.
                if from_index == to_index:
                    travel_times.append(0)
                    objective_distances.append(0)
                    continue

                raw_duration = self._finite_nonnegative(
                    self.distance_matrix.durations[from_index][to_index],
                    f"duration[{from_index}][{to_index}]",
                )
                traffic_multiplier = get_traffic_multiplier(
                    self.location_config,
                    matrix_coords[from_index],
                    matrix_coords[to_index],
                )
                # Match the existing PyVRP quantisation exactly: truncate the
                # OSRM value first and then truncate the traffic-adjusted value.
                duration = int(raw_duration)
                if traffic_multiplier > 1.0:
                    duration = int(duration * float(traffic_multiplier))
                # Service belongs to the source customer.  This preserves
                # arrival-time-window semantics while allowing each vehicle
                # profile to have a different service duration.
                duration += service_by_source[from_index]
                travel_times.append(max(0, duration))

                raw_distance = self._finite_nonnegative(
                    self.distance_matrix.distances[from_index][to_index],
                    f"distance[{from_index}][{to_index}]",
                )
                objective_distance = int(raw_distance)
                if (
                    self._objective_metric() == "distance"
                    and to_index >= len(self.unique_depots)
                ):
                    multiplier, penalty = center_zone_cost_adjustment(
                        matrix_coords[to_index],
                        vehicle_config.vehicle_type,
                        self.location_config,
                        self._objective_penalty_cost,
                    )
                    objective_distance = (
                        int(round(objective_distance * float(multiplier))) + int(penalty)
                    )
                objective_distances.append(max(0, objective_distance))

        return {
            "profile": profile_name,
            "travelTimes": travel_times,
            "distances": objective_distances,
        }

    def _extract_solution(self, response: Dict[str, Any], version: str) -> CVRPSolution:
        tours = response.get("tours", []) or []
        if not isinstance(tours, list):
            raise VRPRustPrototypeError("VRP-Rust response tours must be an array.")

        assigned_job_ids: set[str] = set()
        routes: List[Route] = []
        used_vehicle_ids: set[str] = set()
        response_violations = response.get("violations", []) or []

        for tour in tours:
            if not isinstance(tour, dict):
                raise VRPRustPrototypeError("VRP-Rust returned an invalid tour.")
            vehicle_id = str(tour.get("vehicleId") or "")
            info = self._vehicle_by_id.get(vehicle_id)
            if info is None:
                raise VRPRustPrototypeError(f"VRP-Rust returned unknown vehicle {vehicle_id!r}.")
            if vehicle_id in used_vehicle_ids:
                raise VRPRustPrototypeError(f"VRP-Rust returned vehicle {vehicle_id!r} twice.")
            used_vehicle_ids.add(vehicle_id)

            segments = self._tour_segments(tour, info, assigned_job_ids)
            trip_count = len(segments)
            for trip_index, segment in enumerate(segments, start=1):
                schedule_entries, distance_m, duration_s = self._build_route_schedule_entries(
                    segment.customers,
                    segment.start_location,
                    info.config,
                    segment.end_location,
                    start_time_seconds=segment.start_seconds,
                )
                routes.append(
                    Route(
                        vehicle_type=info.config.vehicle_type,
                        vehicle_id=info.physical_vehicle_id,
                        customers=list(segment.customers),
                        depot_location=segment.start_location,
                        end_location=segment.end_location,
                        vehicle_name=info.vehicle_name,
                        total_distance_km=distance_m / 1000,
                        total_time_minutes=duration_s / 60,
                        total_volume=sum(
                            max(0.0, float(getattr(customer, "volume", 0) or 0))
                            for customer in segment.customers
                        ),
                        is_feasible=True,
                        schedule_entries=schedule_entries,
                        vehicle_key=info.vehicle_key,
                        trip_number=trip_index,
                        trip_count=trip_count,
                        route_id=f"{info.vehicle_key}:trip:{trip_index}",
                        planned_start_minutes=segment.start_seconds / 60,
                        planned_end_minutes=(segment.start_seconds + duration_s) / 60,
                    )
                )

        explicit_unassigned = response.get("unassigned", []) or []
        if not isinstance(explicit_unassigned, list):
            raise VRPRustPrototypeError("VRP-Rust response unassigned must be an array.")
        explicit_ids = {
            str(item.get("jobId") or "")
            for item in explicit_unassigned
            if isinstance(item, dict)
        }
        unknown_unassigned = explicit_ids - set(self._job_by_id)
        if unknown_unassigned:
            raise VRPRustPrototypeError(
                f"VRP-Rust returned unknown unassigned jobs: {sorted(unknown_unassigned)!r}"
            )

        dropped_customers = [
            customer
            for job_id, customer in self._job_by_id.items()
            if job_id not in assigned_job_ids
        ]
        statistic = response.get("statistic", {}) or {}
        if not isinstance(statistic, dict):
            raise VRPRustPrototypeError("VRP-Rust response statistic must be an object.")
        total_distance_km = sum(route.total_distance_km for route in routes)
        total_served_volume = sum(route.total_volume for route in routes)
        try:
            total_time_minutes = float(statistic.get("duration", 0) or 0) / 60
        except (TypeError, ValueError, OverflowError):
            total_time_minutes = sum(route.total_time_minutes for route in routes)
        try:
            fitness = (
                float(statistic.get("duration", math.inf))
                if self._objective_metric() == "time"
                else float(statistic.get("cost", math.inf))
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise VRPRustPrototypeError("VRP-Rust returned an invalid objective value.") from exc

        solution = CVRPSolution(
            routes=routes,
            dropped_customers=dropped_customers,
            total_distance_km=total_distance_km,
            total_time_minutes=total_time_minutes,
            total_vehicles_used=len(used_vehicle_ids),
            fitness_score=fitness,
            is_feasible=bool(routes) and math.isfinite(fitness) and not bool(response_violations),
            total_served_volume=total_served_volume,
            total_trips=len(routes),
            second_trips_count=sum(1 for route in routes if route.trip_number > 1),
            solver_requested="vrp",
            solver_used="vrp",
            solver_backend="vrp",
            solver_version=version,
            solver_fallback_used=False,
            solver_fallback_reason="",
        )

        violations = self._validate_extracted_solution_constraints(solution)
        if violations:
            logger.error(
                "VRP-Rust result failed the independent hard-constraint audit: %s",
                "; ".join(violations),
            )
            solution.is_feasible = False
            solution.fitness_score = math.inf
            for route in solution.routes:
                route.is_feasible = False
        return solution

    def _tour_segments(
        self,
        tour: Dict[str, Any],
        info: _VehicleInfo,
        assigned_job_ids: set[str],
    ) -> List[_TripSegment]:
        stops = tour.get("stops", []) or []
        if not isinstance(stops, list) or not stops:
            return []

        first_time = stops[0].get("time", {}) if isinstance(stops[0], dict) else {}
        segment_start_seconds = self._seconds_from_iso(first_time.get("departure"))
        segment_start_location = info.start_location
        current_customers: List[Customer] = []
        segments: List[_TripSegment] = []

        for stop in stops[1:]:
            if not isinstance(stop, dict):
                raise VRPRustPrototypeError("VRP-Rust returned an invalid stop.")
            stop_time = stop.get("time", {}) or {}
            activities = stop.get("activities", []) or []
            if not isinstance(activities, list):
                raise VRPRustPrototypeError("VRP-Rust returned invalid stop activities.")

            is_reload = False
            is_arrival = False
            for activity in activities:
                if not isinstance(activity, dict):
                    continue
                job_id = str(activity.get("jobId") or "")
                activity_type = str(activity.get("type") or "").lower()
                if job_id == "reload" or activity_type == "reload":
                    is_reload = True
                    continue
                if job_id == "arrival" or activity_type == "arrival":
                    is_arrival = True
                    continue
                if job_id == "departure" or activity_type == "departure":
                    continue
                customer = self._job_by_id.get(job_id)
                if customer is None:
                    raise VRPRustPrototypeError(f"VRP-Rust returned unknown job {job_id!r}.")
                if job_id in assigned_job_ids:
                    raise VRPRustPrototypeError(f"VRP-Rust assigned job {job_id!r} more than once.")
                assigned_job_ids.add(job_id)
                current_customers.append(customer)

            if is_reload:
                if current_customers:
                    segments.append(
                        _TripSegment(
                            customers=current_customers,
                            start_location=segment_start_location,
                            end_location=info.reload_location,
                            start_seconds=segment_start_seconds,
                        )
                    )
                current_customers = []
                segment_start_location = info.reload_location
                segment_start_seconds = self._seconds_from_iso(stop_time.get("departure"))

            if is_arrival and current_customers:
                segments.append(
                    _TripSegment(
                        customers=current_customers,
                        start_location=segment_start_location,
                        end_location=info.end_location,
                        start_seconds=segment_start_seconds,
                    )
                )
                current_customers = []

        if current_customers:
            segments.append(
                _TripSegment(
                    customers=current_customers,
                    start_location=segment_start_location,
                    end_location=info.end_location,
                    start_seconds=segment_start_seconds,
                )
            )
        return segments

    @staticmethod
    def _iso_time(seconds: int) -> str:
        return (_BASE_INSTANT + timedelta(seconds=max(0, int(seconds)))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    @staticmethod
    def _seconds_from_iso(value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            raise VRPRustPrototypeError("VRP-Rust stop is missing a timestamp.")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise VRPRustPrototypeError(f"Invalid VRP-Rust timestamp: {text!r}") from exc
        return max(0.0, (parsed - _BASE_INSTANT).total_seconds())

    @classmethod
    def _scaled_volume(cls, value: Any, label: str) -> int:
        numeric = cls._finite_nonnegative(value, label)
        return max(0, int(round(numeric * _VOLUME_SCALE)))

    @staticmethod
    def _finite_nonnegative(value: Any, label: str) -> float:
        """Accept sub-metre/second OSRM floating point noise, reject real negatives."""
        if isinstance(value, bool):
            numeric = float(int(value))
        else:
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise VROOMSolverError(f"Invalid {label}: {value!r}.") from exc
        if not math.isfinite(numeric):
            raise VROOMSolverError(f"Invalid {label}: expected a finite value, got {value!r}.")
        if numeric < 0:
            matrix_label = label.startswith(("distance[", "distances[", "duration[", "durations["))
            if matrix_label and numeric >= -1.0:
                logger.debug("Clamping tiny negative OSRM %s=%s to zero", label, numeric)
                return 0.0
            raise VROOMSolverError(
                f"Invalid {label}: expected a non-negative value, got {value!r}."
            )
        return numeric


__all__ = ["VRPRustPrototype", "VRPRustPrototypeError"]
