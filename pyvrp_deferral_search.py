"""Bounded regret/ejection repair inside the PyVRP 0.14 ILS search path.

No invented tomorrow routes. All acceptance uses native prizes + today's cost.
Only complete, feasible, independently audited improving moves are returned.
"""
from collections import Counter, defaultdict
import logging
import time

from pyvrp import Activity, ActivityType, Route, Solution, RandomNumberGenerator
from pyvrp.IteratedLocalSearch import IteratedLocalSearch
from pyvrp.PenaltyManager import PenaltyManager
from pyvrp.search import LocalSearch, PerturbationManager, compute_neighbours
from importlib.metadata import version as package_version

logger = logging.getLogger(__name__)


class GeographicRepair:
    def __init__(self, data, original_indices, max_ejections=6, audit=None):
        self.data = data
        self.original = original_indices
        self.audit = audit or (lambda solution: True)
        self.max_ejections = max_ejections
        self.alternatives = defaultdict(list)
        for idx, original in enumerate(original_indices):
            self.alternatives[original].append(idx)
        self.required = {
            original for original, alternatives in self.alternatives.items()
            if any(data.client(idx).required or (
                data.client(idx).group is not None and
                data.group(data.client(idx).group).required) for idx in alternatives)
        }
        self.prizes = {original: max(data.client(idx).prize for idx in alternatives)
                       for original, alternatives in self.alternatives.items()}
        self.stats = dict(calls=0, evaluated=0, accepted=0, compound_accepted=0,
                          audit_rejected=0, seconds=0.0, max_ejected=0)
        self.cursor = 0

    def served(self, solution):
        return {self.original[a.idx] for route in solution.routes() for a in route if a.is_client()}

    @staticmethod
    def activities(route):
        # Native route iteration includes its start and end depots.
        return [Activity(a.type, a.idx) for a in list(route)[1:-1]]

    def plan(self, routes):
        return Solution(self.data, [r for r in routes if r.num_clients()])

    def remove(self, route, removed):
        activities = [a for a in self.activities(route)
                      if not a.is_client() or self.original[a.idx] not in removed]
        # Drop empty trips, preserving all meaningful reloads.
        cleaned = []
        for a in activities:
            if a.is_depot() and (not cleaned or cleaned[-1].is_depot()):
                continue
            cleaned.append(a)
        while cleaned and cleaned[-1].is_depot():
            cleaned.pop()
        return Route(self.data, cleaned, route.vehicle_type())

    def insertions(self, solution, original, evaluator, deadline, only_route=None):
        """Best two feasible positions, including alternate windows and spare vans."""
        routes = list(solution.routes())
        usage = Counter(r.vehicle_type() for r in routes)
        targets = [(idx, route) for idx, route in enumerate(routes)
                   if only_route is None or idx == only_route]
        if only_route is None:
            for vt, vehicle in enumerate(self.data.vehicle_types()):
                if usage[vt] < vehicle.num_available:
                    targets.append((len(routes), Route(self.data, [], vt)))
        options = []
        for idx, route in targets:
            activities = self.activities(route)
            for client in self.alternatives[original]:
                activity = Activity(ActivityType.CLIENT, client)
                sequences = (activities[:pos] + [activity] + activities[pos:]
                             for pos in range(len(activities) + 1))
                # Also try an extra final trip where this vehicle permits it.
                vehicle = self.data.vehicle_type(route.vehicle_type())
                reloads = []
                if route.num_clients() and route.num_trips() <= vehicle.max_reloads:
                    reloads = [activities + [Activity(ActivityType.DEPOT, depot), activity]
                               for depot in vehicle.reload_depots]
                from itertools import chain
                for sequence in chain(sequences, reloads):
                    if time.monotonic() >= deadline:
                        return sorted(options, key=lambda x: x[0])[:2]
                    changed = Route(self.data, sequence, route.vehicle_type())
                    self.stats["evaluated"] += 1
                    if not changed.is_feasible():
                        continue
                    candidate_routes = routes.copy()
                    if idx == len(routes):
                        candidate_routes.append(changed)
                    else:
                        candidate_routes[idx] = changed
                    candidate = self.plan(candidate_routes)
                    if candidate.is_feasible():
                        options.append((evaluator.cost(candidate), candidate))
                        options.sort(key=lambda x: x[0])
                        del options[2:]
        return options

    def regret_reinsert(self, solution, originals, evaluator, deadline):
        pending = set(originals) - self.served(solution)
        while pending and time.monotonic() < deadline:
            choices = []
            for original in sorted(pending, key=lambda x: (-self.prizes[x], x)):
                options = self.insertions(solution, original, evaluator, deadline)
                if options and options[0][0] < evaluator.cost(solution):
                    # One feasible opportunity receives maximum scarcity priority.
                    regret = options[1][0] - options[0][0] if len(options) > 1 else self.prizes[original]
                    choices.append((regret, self.prizes[original], original, options[0][1]))
                if time.monotonic() >= deadline:
                    break
            if not choices:
                break
            _, _, original, solution = max(choices, key=lambda item: item[:3])
            pending.remove(original)
        return solution

    def improve(self, solution, evaluator, deadline):
        self.stats["calls"] += 1
        if not solution.is_feasible():
            return solution
        best = solution
        pending = sorted(set(self.alternatives) - self.served(best),
                         key=lambda x: (-self.prizes[x], x))
        # Rotate the shortlist so a permanently infeasible first client does not
        # consume every repair budget. Priorities still order each shortlist.
        if pending:
            start = self.cursor % len(pending)
            self.cursor += 1
            pending = (pending[start:] + pending[:start])[:8]
        # Reserve most of this slice for compound moves even if all pending
        # clients have many possible insertion positions.
        now = time.monotonic()
        direct = self.regret_reinsert(best, pending, evaluator, now + max(0, deadline - now) * 0.2)
        best = self.accept(best, direct, evaluator, 0)
        for original in pending:
            if original in self.served(best) or time.monotonic() >= deadline:
                continue
            for route_idx, route in enumerate(best.routes()):
                removable = {self.original[a.idx] for a in route if a.is_client()
                             and self.original[a.idx] not in self.required
                             and self.prizes[self.original[a.idx]] < self.prizes[original]}
                # Two bounded prefixes: lowest protection and protection/load.
                def load(key):
                    return max(1, sum(self.data.client(self.alternatives[key][0]).delivery))
                orderings = [sorted(removable, key=lambda x: (self.prizes[x], x)),
                             sorted(removable, key=lambda x: (self.prizes[x] / load(x), x))]
                seen = set()
                for ordered in orderings:
                    for count in range(1, min(len(ordered), self.max_ejections) + 1):
                        if time.monotonic() >= deadline:
                            return best
                        removed = frozenset(ordered[:count])
                        if removed in seen:
                            continue
                        seen.add(removed)
                        routes = list(best.routes())
                        routes[route_idx] = self.remove(route, removed)
                        partial = self.plan(routes)
                        # Try all compatible routes: this allows relocation, not
                        # only exchange into the route that released resources.
                        options = self.insertions(partial, original, evaluator, deadline)
                        if not options:
                            continue
                        candidate = self.regret_reinsert(options[0][1], removed, evaluator, deadline)
                        accepted = self.accept(best, candidate, evaluator, count)
                        if accepted is not best:
                            # Route indices/removal sets are stale after acceptance.
                            return accepted
        return best

    def accept(self, incumbent, candidate, evaluator, ejected):
        if candidate is incumbent or not candidate.is_feasible() or evaluator.cost(candidate) >= evaluator.cost(incumbent):
            return incumbent
        visits = [self.original[a.idx] for route in candidate.routes() for a in route if a.is_client()]
        if len(visits) != len(set(visits)) or not self.required.issubset(visits) or not self.audit(candidate):
            self.stats["audit_rejected"] += 1
            return incumbent
        self.stats["accepted"] += 1
        self.stats["compound_accepted"] += bool(ejected)
        self.stats["max_ejected"] = max(self.stats["max_ejected"], ejected)
        # Per-move diagnostics are verbose; the final aggregate stays at INFO.
        logger.debug("Geographic repair accepted: cost %s -> %s, served %s -> %s, ejection set=%s",
                    evaluator.cost(incumbent), evaluator.cost(candidate),
                    incumbent.num_clients(), candidate.num_clients(), ejected)
        return candidate


def solve_geographic(data, *, seed, params, display, seconds, config, original_indices, audit):
    """Mirror the installed 0.14 solve factory, wrapping its SearchMethod only."""
    started = time.monotonic()
    deadline = started + seconds
    rng = RandomNumberGenerator(seed=seed)
    # 1.x requires ProblemData to preserve mutually exclusive groups during
    # perturbation. Keep the stable 0.14 call unchanged.
    if int(package_version("pyvrp").split(".")[0]) >= 1:
        perturbation = PerturbationManager(data, params.perturbation)
    else:
        perturbation = PerturbationManager(params.perturbation)
    ls = LocalSearch(data, rng, compute_neighbours(data, params.neighbourhood),
                     perturbation)
    for operator in params.operators:
        if operator.supports(data):
            ls.add_operator(operator(data))
    pm = PenaltyManager(params.penalty.midpoint_penalties(data), params.penalty)
    repair = GeographicRepair(data, original_indices, config.drop_repair_max_ejections, audit)
    budget = seconds * config.drop_repair_budget_fraction
    slice_seconds = min(0.75, budget)
    # Spread the reserved share across the run. Spending all repair time on
    # early, poor incumbents would miss useful exchanges found later by ILS.
    repair_interval = slice_seconds / config.drop_repair_budget_fraction
    last_repair = 0.0

    def search(solution, evaluator, exhaustive=False):
        nonlocal last_repair
        result = ls(solution, evaluator, exhaustive=exhaustive)
        now = time.monotonic()
        remaining = budget - repair.stats["seconds"]
        if result.is_feasible() and now - last_repair >= repair_interval and remaining > 0 and now < deadline:
            last_repair = now
            result = repair.improve(result, evaluator, min(deadline, now + remaining, now + slice_seconds))
            repair.stats["seconds"] += time.monotonic() - now
        return result

    init = search(Solution.make_random(data, rng), pm.max_cost_evaluator(), exhaustive=True)
    algo = IteratedLocalSearch(data, pm, search, init, params.ils)
    result = algo.run(lambda cost: time.monotonic() >= deadline, True, display, params.display_interval)
    logger.info("Geographic repair summary: %s; total search seconds=%.3f", repair.stats, time.monotonic() - started)
    return result, repair.stats
