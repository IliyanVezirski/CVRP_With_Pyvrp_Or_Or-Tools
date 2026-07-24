import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import LocationConfig
from pyvrp_solver import PyVRPSolver

from tests.test_pyvrp_multitrip import (
    DEPOT,
    make_config,
    make_customers,
    make_matrix,
    make_vehicle,
)


class FakeActivity:
    def __init__(self, kind, idx, trip):
        self.kind = kind
        self.idx = idx
        self.trip = trip

    def is_client(self):
        return self.kind == "client"

    def is_depot(self):
        return self.kind == "depot"


class FakeRoute:
    def __init__(self, activities, vehicle_type=0):
        self.activities = activities
        self._vehicle_type = vehicle_type

    def __iter__(self):
        return iter(self.activities)

    def vehicle_type(self):
        return self._vehicle_type


class FakeBest:
    def __init__(self, routes):
        self._routes = routes

    def routes(self):
        return self._routes


class FakeResult:
    def __init__(self, routes, cost=123):
        self.best = FakeBest(routes)
        self._cost = cost

    def cost(self):
        return self._cost

    def is_feasible(self):
        return True


class FakeProblemData:
    def __init__(self, vehicle_type, reload_seconds=300):
        self._vehicle_types = [vehicle_type]
        self._reload_seconds = reload_seconds

    def vehicle_types(self):
        return self._vehicle_types

    def depot(self, idx):
        return SimpleNamespace(
            service_duration=self._reload_seconds if idx == 1 else 0
        )


class PyVRPNextActivityAdapterTests(unittest.TestCase):
    def make_solver(self, customers, depots):
        reload_location = depots[1]
        vehicle = make_vehicle(
            reload_location=reload_location,
            end_location=DEPOT,
        )
        solver = PyVRPSolver(
            make_config(),
            [vehicle],
            customers,
            make_matrix(customers, depots),
            depots,
            [],
            LocationConfig(depot_location=DEPOT),
        )
        solver._pyvrp_depot_coords = list(depots)
        return solver

    def test_scheduled_activities_split_reload_courses_at_depots(self):
        reload_location = (42.1, 23.1)
        customers = make_customers(3, volume=1)
        solver = self.make_solver(customers, [DEPOT, reload_location])
        solver._pyvrp_client_to_customer_idx = [0, 1, 2]

        route = FakeRoute([
            FakeActivity("depot", 0, 0),
            FakeActivity("client", 0, 0),
            FakeActivity("depot", 1, 1),
            FakeActivity("client", 1, 1),
            FakeActivity("client", 2, 1),
            FakeActivity("depot", 0, 2),
        ])
        vehicle_type = SimpleNamespace(
            num_available=1,
            start_depot=0,
            end_depot=0,
            name="internal_bus",
        )

        with patch("pyvrp_solver.PYVRP_LOCATION_API", True):
            solution = solver._extract_solution(
                SimpleNamespace(depots=[object(), object()]),
                FakeProblemData(vehicle_type),
                FakeResult([route]),
            )

        self.assertTrue(solution.is_feasible)
        self.assertEqual(1, solution.total_vehicles_used)
        self.assertEqual(2, solution.total_trips)
        self.assertEqual([], solution.dropped_customers)
        self.assertEqual([1, 2], [course.trip_number for course in solution.routes])
        self.assertEqual(reload_location, solution.routes[0].end_location)
        self.assertEqual(reload_location, solution.routes[1].depot_location)
        self.assertEqual(DEPOT, solution.routes[1].end_location)
        self.assertAlmostEqual(
            solution.routes[0].planned_end_minutes + 5,
            solution.routes[1].planned_start_minutes,
        )

    def test_client_activity_index_maps_directly_for_time_window_alternatives(self):
        customers = make_customers(2, volume=1)
        solver = self.make_solver(customers, [DEPOT, (42.1, 23.1)])
        # Client indices 0 and 1 are two time-window alternatives for the same
        # logical customer.  Index 2 is the second logical customer.
        solver._pyvrp_client_to_customer_idx = [0, 0, 1]
        route = FakeRoute([
            FakeActivity("depot", 0, 0),
            FakeActivity("client", 1, 0),
            FakeActivity("client", 2, 0),
            FakeActivity("depot", 0, 1),
        ])
        vehicle_type = SimpleNamespace(
            num_available=1,
            start_depot=0,
            end_depot=0,
            name="internal_bus",
        )

        with patch("pyvrp_solver.PYVRP_LOCATION_API", True):
            solution = solver._extract_solution(
                SimpleNamespace(depots=[object(), object()]),
                FakeProblemData(vehicle_type),
                FakeResult([route]),
            )

        self.assertEqual(
            [customers[0].id, customers[1].id],
            [customer.id for customer in solution.routes[0].customers],
        )
        self.assertEqual([], solution.dropped_customers)


if __name__ == "__main__":
    unittest.main()
