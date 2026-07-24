"""Run the gated VRP-Rust benchmark against the last valid PyVRP run.

This script has no GUI/API/build side effects and does not export or upload a
solution.  It only loads the same dated HTTP input, builds the same OSRM
matrix, solves it with the benchmark adapter, and prints a compact comparison.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import sys
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent
DIST_DIR = SOURCE_DIR.parent / "dist"

# Load the config which produced the deployed run, while keeping source code
# modules available for the benchmark adapter.
sys.path.insert(0, str(DIST_DIR))
sys.path.insert(1, str(SOURCE_DIR))

import config  # noqa: E402
import main  # noqa: E402
from config import build_ordered_depots  # noqa: E402
from vrp_rust_prototype import VRPRustPrototype  # noqa: E402


PYVRP_BASELINE = {
    "run_id": "cvrp.log@2026-07-15T17:39:06",
    "date": "16/07/2026",
    "done_flag": "1974",
    "customers": 263,
    "volume": 2775.17,
    "dropped": 0,
    "fitness": 850535.0,
    "distance_km": 853.26,
    "time_minutes": 3645.6,
    "vehicles": 8,
    "trips": 9,
}


def _summary(solution, customer_count: int, input_volume: float) -> dict:
    return {
        "solver": "reinterpretcat_vrp",
        "version": str(solution.solver_version or "unknown"),
        "input_customers": customer_count,
        "input_volume": round(input_volume, 2),
        "served_customers": sum(len(route.customers) for route in solution.routes),
        "served_volume": round(float(solution.total_served_volume or 0), 2),
        "dropped": len(solution.dropped_customers),
        "fitness": float(solution.fitness_score),
        "distance_km": round(float(solution.total_distance_km or 0), 3),
        "time_minutes": round(float(solution.total_time_minutes or 0), 3),
        "vehicles": int(solution.total_vehicles_used or 0),
        "trips": int(solution.total_trips or len(solution.routes)),
        "second_trips": int(solution.second_trips_count or 0),
        "feasible": bool(solution.is_feasible),
    }


def run_benchmark(time_limit: int, date_text: str, require_baseline_match: bool) -> dict:
    active_config = copy.deepcopy(config.get_config())
    active_config.input.json_override_date = date_text
    active_config.input.json_done_flag = PYVRP_BASELINE["done_flag"]
    active_config.cvrp.objective_metric = "distance"
    active_config.cvrp.time_limit_seconds = int(time_limit)

    with main._temporary_runtime_config(active_config):
        input_data, allocation = main.prepare_data(None, config_override=active_config)
        if not input_data or not allocation:
            raise RuntimeError("Benchmark input could not be loaded.")
        allocation = main.move_customers_without_coordinates_to_unserved(allocation)
        active_vehicles = main.get_active_vehicle_configs(allocation, active_config)

        actual_clients = len(allocation.vehicle_customers)
        actual_volume = float(allocation.total_vehicle_volume or 0)
        baseline_match = (
            actual_clients == PYVRP_BASELINE["customers"]
            and abs(actual_volume - PYVRP_BASELINE["volume"]) <= 0.02
        )
        print(
            "BENCHMARK_INPUT "
            + json.dumps(
                {
                    "date": date_text,
                    "customers": actual_clients,
                    "volume": round(actual_volume, 2),
                    "matches_pyvrp_baseline": baseline_match,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if require_baseline_match and not baseline_match:
            raise RuntimeError(
                "The dated HTTP input no longer matches the recorded PyVRP run; "
                "a comparison against its score would not be valid."
            )

        matrix = main.get_distance_matrix(
            allocation,
            active_config.locations,
            active_vehicles,
        )
        if matrix is None:
            raise RuntimeError("Benchmark OSRM matrix could not be built.")

        depots = build_ordered_depots(
            active_config.locations.depot_location,
            active_vehicles,
            include_reload_locations=bool(active_config.cvrp.enable_multiple_trips),
        )
        solver = VRPRustPrototype(
            active_config.cvrp,
            active_vehicles,
            list(allocation.vehicle_customers),
            matrix,
            depots,
            list(allocation.center_zone_customers or []),
            active_config.locations,
            time_limit_seconds=time_limit,
            thread_pools=4,
            threads_per_pool=4,
        )
        solution = solver.solve()

    rust_summary = _summary(solution, actual_clients, actual_volume)
    comparable = baseline_match and rust_summary["feasible"]
    better = bool(
        comparable
        and rust_summary["dropped"] <= PYVRP_BASELINE["dropped"]
        and math.isfinite(rust_summary["fitness"])
        and rust_summary["fitness"] < PYVRP_BASELINE["fitness"] - 0.5
    )
    result = {
        "comparable": comparable,
        "better_than_pyvrp": better,
        "pyvrp_baseline": PYVRP_BASELINE,
        "vrp_rust": rust_summary,
        "fitness_delta": (
            rust_summary["fitness"] - PYVRP_BASELINE["fitness"]
            if comparable and math.isfinite(rust_summary["fitness"])
            else None
        ),
    }
    print("BENCHMARK_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main_cli() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Gated VRP-Rust benchmark")
    parser.add_argument("--time-limit", type=int, default=360)
    parser.add_argument("--date", default=PYVRP_BASELINE["date"])
    parser.add_argument("--allow-input-mismatch", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    run_benchmark(
        max(1, args.time_limit),
        args.date,
        require_baseline_match=not args.allow_input_mismatch,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
