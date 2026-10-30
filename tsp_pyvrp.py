"""Single-driver TSP solved only by the isolated, pinned PyVRP runtime.

The callback observes PyVRP-generated orders; it does not construct or modify
orders. It retains complete, softly penalised alternatives when time windows
cannot all be satisfied, using a real forward clock (never time-warped ETAs).
"""
from __future__ import annotations

import math
from types import SimpleNamespace

from pyvrp_next_version import PYVRP_NEXT_VERSION

TSP_CAPABILITY = "current_tsp_soft_windows_v1"
DEFAULT_SEARCH_SECONDS = 10.0


def search_seconds(payload, config):
    raw = payload.get("tsp_search_seconds", getattr(getattr(config, "api", None), "tsp_search_seconds", DEFAULT_SEARCH_SECONDS))
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("TSP време за търсене: въведи от 1 до 120 секунди.") from exc
    if not math.isfinite(seconds) or not 1 <= seconds <= 120:
        raise ValueError("TSP време за търсене: въведи от 1 до 120 секунди.")
    return seconds


def evaluate_order(spec, order):
    """Score an observed order with actual arrival/wait/late times in seconds."""
    clock = spec["start_seconds"]
    distance = wait = late = 0.0
    current = 0
    late_count = 0
    for node in order:
        distance += spec["distances"][current][node]
        clock += spec["durations"][current][node]
        windows = spec["clients"][node - 1]["windows"]
        if windows:
            available = next((window for window in windows if clock <= window[1]), None)
            if available:
                waiting = max(0.0, available[0] - clock)
                wait += waiting
                clock += waiting
            else:
                late += clock - windows[-1][1]
                late_count += 1
        clock += spec["clients"][node - 1]["service_seconds"]
        current = node
    distance += spec["distances"][current][-1]
    clock += spec["durations"][current][-1]
    elapsed = clock - spec["start_seconds"]
    # Service is constant for all orders. Preserve the historical wait/late
    # coefficients, without adding waiting twice in time mode.
    base = distance if spec["metric"] == "distance" else elapsed - wait
    cost = base + spec["wait_weight"] * wait + spec["late_weight"] * late
    return dict(distance_m=distance, duration_seconds=elapsed, wait_seconds=wait,
                late_seconds=late, late_count=late_count, penalised_cost=cost)


def solve_native(spec):
    """Runs inside the PyVRP worker, never in the application's 0.13 runtime."""
    from pyvrp import Model, SolveParams
    from pyvrp.IteratedLocalSearch import IteratedLocalSearchCallbacks, IteratedLocalSearchParams
    from pyvrp.stop import MaxRuntime

    count = len(spec["clients"])
    size = count + 2
    if not count or spec["metric"] not in {"time", "distance"}:
        raise ValueError("Invalid TSP model")
    for key in ("distances", "durations"):
        values = spec[key]
        if len(values) != size or any(len(row) != size for row in values):
            raise ValueError("Invalid TSP matrix dimensions")
        if any(not math.isfinite(v) or not 0 <= v < 1e10 for row in values for v in row):
            raise ValueError("Невалидни пътни разстояния/времена за TSP.")
    start = float(spec["start_seconds"])
    services = [float(c["service_seconds"]) for c in spec["clients"]]
    if not math.isfinite(start) or start < 0 or any(not math.isfinite(s) or s < 0 for s in services):
        raise ValueError("Invalid TSP service/start times")
    windows = [c["windows"] for c in spec["clients"]]
    for sequence in windows:
        if any(len(w) != 2 or not all(math.isfinite(v) for v in w) or not 0 <= w[0] <= w[1] for w in sequence):
            raise ValueError("Invalid TSP time windows")
        if sequence != sorted(sequence):
            raise ValueError("Unsorted TSP time windows")
    horizon = max(start, max((w[1] for seq in windows for w in seq), default=0))
    horizon += sum(max(row) for row in spec["durations"]) + sum(services) + 86400
    # Milliseconds and millimetres avoid artificial second rounding violations.
    scale = lambda v: int(round(v * 1000))
    model = Model()
    locations = [model.add_location(x=lon, y=lat) for lat, lon in spec["locations"]]
    depot = model.add_depot(locations[0], tw_early=scale(start), tw_late=scale(horizon))
    end = model.add_depot(locations[-1], tw_late=scale(horizon))
    client_nodes = []
    for node, client in enumerate(spec["clients"], 1):
        alternatives = client["windows"] or [[0, horizon]]
        group = model.add_client_group(required=True) if len(alternatives) > 1 else None
        for early, last in alternatives:
            model.add_client(locations[node], service_duration=scale(client["service_seconds"]),
                             tw_early=scale(early), tw_late=scale(last),
                             required=group is None, group=group, name=f"stop-{node}")
            client_nodes.append(node)
    model.add_vehicle_type(num_available=1, start_depot=depot, end_depot=end,
                           tw_early=scale(start), start_late=scale(start), tw_late=scale(horizon),
                           unit_distance_cost=int(spec["metric"] == "distance"),
                           unit_duration_cost=int(spec["metric"] == "time"), max_reloads=0)
    for i, origin in enumerate(locations):
        for j, destination in enumerate(locations):
            model.add_edge(origin, destination, distance=scale(spec["distances"][i][j]),
                           duration=scale(spec["durations"][i][j]))

    class Observer(IteratedLocalSearchCallbacks):
        best_order = None
        best_key = None
        best_metrics = None
        last_order = None

        def consider(self, solution):
            if not solution.is_complete() or solution.num_routes() != 1:
                return
            route = solution.routes()[0]
            order = tuple(client_nodes[a.idx] for a in route if a.is_client())
            if order == self.last_order:
                return
            self.last_order = order
            # Identity is the input row, not IdCust: distinct visits may share ID.
            if sorted(order) != list(range(1, count + 1)):
                return
            metrics = evaluate_order(spec, order)
            key = (metrics["late_count"] > 0, metrics["penalised_cost"])
            if self.best_key is None or key < self.best_key:
                self.best_key, self.best_order, self.best_metrics = key, list(order), metrics

        def on_start(self, ils):
            self.consider(ils.initial_solution)

        def on_iteration(self, current, candidate, best, cost_evaluator):
            self.consider(candidate)
            self.consider(current)
            self.consider(best)

        def on_best(self, best):
            self.consider(best)

        def on_end(self, result):
            self.consider(result.best)

    observer = Observer()
    result = model.solve(MaxRuntime(float(spec["search_seconds"])), seed=0,
                         collect_stats=False, display=False,
                         params=SolveParams(ils=IteratedLocalSearchParams(callbacks=observer)))
    if observer.best_order is None:
        raise RuntimeError("PyVRP не върна маршрут с всички клиенти точно веднъж.")
    return dict(order=observer.best_order, **observer.best_metrics,
                search_runtime_seconds=result.runtime, iterations=result.num_iterations,
                time_windows_satisfied=observer.best_metrics["late_count"] == 0)


def selected_backend(config):
    from pyvrp_backends import STABLE, PATCH_0141
    name = str(getattr(getattr(config, 'api', config), 'tsp_solver_type', STABLE.public))
    choices = {STABLE.public: STABLE, PATCH_0141.public: PATCH_0141}
    if name not in choices:
        raise ValueError('TSP решител: избери pyvrp_experimental (0.14.0) или pyvrp_0141 (0.14.1).')
    return choices[name]


def _worker_command(config):
    from pyvrp_next_runtime import _resolve_worker_command
    backend = selected_backend(config)
    settings = SimpleNamespace(solver_type=backend.public,
        **{backend.path_setting: getattr(config.cvrp, backend.path_setting, "")})
    return _resolve_worker_command(settings)


def warmup(config):
    from tsp_pyvrp_runtime import worker_for
    worker = worker_for(selected_backend(config).version)
    worker.warmup(_worker_command(config))


def solve_order(spec, config):
    """Reuse the versioned native worker; never fall back to our old heuristic."""
    from tsp_pyvrp_runtime import worker_for
    worker = worker_for(selected_backend(config).version)
    result = worker.solve(_worker_command(config), spec)
    order = result.get("order", [])
    if any(type(n) is not int for n in order) or sorted(order) != list(range(1, len(spec["clients"]) + 1)):
        raise RuntimeError("PyVRP върна липсващи или дублирани TSP клиенти.")
    return result
