"""Application-facing adapter for the experimental reinterpretcat/vrp solver."""

from __future__ import annotations

from typing import List, Optional, Tuple

from config import CVRPConfig, LocationConfig, VehicleConfig, build_ordered_depots
from cvrp_solver import CVRPSolution
from osrm_client import DistanceMatrix
from vrp_rust_prototype import VRPRustPrototype, VRPRustPrototypeError
from warehouse_manager import WarehouseAllocation


VRPSolver = VRPRustPrototype
VRPSolverError = VRPRustPrototypeError


def solve_cvrp_vrp(
    allocation: WarehouseAllocation,
    depot_location: Tuple[float, float],
    distance_matrix: DistanceMatrix,
    config: Optional[CVRPConfig] = None,
    location_config: Optional[LocationConfig] = None,
    vehicle_configs: Optional[List[VehicleConfig]] = None,
) -> CVRPSolution:
    """Solve the application's CVRP input through the isolated Rust runtime."""
    from config import get_config

    full_config = get_config()
    solver_config = config or full_config.cvrp
    vehicles = vehicle_configs if vehicle_configs is not None else list(full_config.vehicles or [])
    locations = location_config or full_config.locations
    unique_depots = build_ordered_depots(
        depot_location,
        vehicles,
        include_reload_locations=bool(getattr(solver_config, "enable_multiple_trips", False)),
    )
    solver = VRPSolver(
        solver_config,
        vehicles,
        list(allocation.vehicle_customers or []),
        distance_matrix,
        unique_depots,
        list(allocation.center_zone_customers or []),
        locations,
    )
    return solver.solve()


__all__ = ["VRPSolver", "VRPSolverError", "solve_cvrp_vrp"]
