import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import VehicleType
from input_handler import Customer
from osrm_client import DistanceMatrix
from pyvrp_solver import PYVRP_AVAILABLE, PyVRPSolver
from vroom_solver import VROOMSolver
from vrp_rust_prototype import VRPRustPrototype


DEPOT = (42.0, 23.0)
CLIENT = (42.01, 23.01)


def make_customer(*, service_time_minutes=1.001, time_windows=None):
    return Customer(
        id="boundary-client",
        name="Boundary client",
        coordinates=CLIENT,
        volume=1,
        original_gps_data="",
        service_time_minutes=service_time_minutes,
        time_windows=list(time_windows or []),
    )


def make_matrix(raw_duration=60.4):
    return DistanceMatrix(
        distances=[[0.0, 1000.0], [1000.0, 0.0]],
        durations=[[0.0, raw_duration], [raw_duration, 0.0]],
        locations=[DEPOT, CLIENT],
        sources=[0, 1],
        destinations=[0, 1],
    )


def make_config(*, time_windows=False, objective_metric="time"):
    return SimpleNamespace(
        enable_customer_time_windows=time_windows,
        allow_customer_skipping=True,
        objective_metric=objective_metric,
        time_objective_include_waiting=True,
        enable_multiple_trips=False,
        global_start_time_minutes=0,
        time_limit_seconds=1,
    )


def make_vehicle(*, service_time_minutes=1.001):
    return SimpleNamespace(
        enabled=True,
        config_id="physical-van",
        vehicle_type=VehicleType.INTERNAL_BUS,
        capacity=100,
        count=1,
        service_time_minutes=service_time_minutes,
        reload_time_minutes=0,
        max_customers_per_day=None,
        max_customers_per_route=100,
        max_distance_km=None,
        max_time_hours=24,
        start_time_minutes=0,
        start_location=DEPOT,
        end_location=DEPOT,
    )


def make_solver(
    solver_type=PyVRPSolver,
    *,
    customer=None,
    vehicle=None,
    config=None,
):
    customer = customer or make_customer()
    vehicle = vehicle or make_vehicle()
    config = config or make_config()
    kwargs = {}
    if solver_type is VRPRustPrototype:
        kwargs.update(
            time_limit_seconds=1,
            thread_pools=1,
            threads_per_pool=1,
        )
    return solver_type(
        config,
        [vehicle],
        [customer],
        make_matrix(),
        [DEPOT],
        [],
        None,
        **kwargs,
    )


class SolverTimeConsistencyTests(unittest.TestCase):
    def test_canonical_clock_ceil_is_single_and_conservative(self):
        self.assertEqual(
            88,
            PyVRPSolver._canonical_travel_seconds(60.4, 1.45),
        )
        self.assertEqual(
            426,
            PyVRPSolver._canonical_service_seconds(7.1),
        )
        self.assertEqual(
            61,
            PyVRPSolver._canonical_service_seconds(1.001),
        )

    def test_route_schedule_uses_canonical_travel_and_service_seconds(self):
        customer = make_customer()
        vehicle = make_vehicle()
        solver = make_solver(customer=customer, vehicle=vehicle)

        with patch("pyvrp_solver.get_traffic_multiplier", return_value=1.45):
            entries, _, total_seconds = solver._build_route_schedule_entries(
                [customer],
                DEPOT,
                vehicle,
                DEPOT,
                start_time_seconds=0,
            )

        self.assertEqual(88, entries[0]["travel_time_minutes"] * 60)
        self.assertEqual(61, entries[0]["service_time_minutes"] * 60)
        self.assertEqual(88, entries[0]["arrival_time_seconds"])
        self.assertEqual(88 + 61 + 88, total_seconds)

    @unittest.skipUnless(PYVRP_AVAILABLE, "PyVRP is not installed")
    def test_pyvrp_model_edges_use_the_same_canonical_clock(self):
        customer = make_customer(time_windows=[(600, 840)])
        vehicle = make_vehicle()
        solver = make_solver(
            customer=customer,
            vehicle=vehicle,
            config=make_config(time_windows=True),
        )

        with patch("pyvrp_solver.get_traffic_multiplier", return_value=1.45):
            model = solver._create_pyvrp_model()
            model_travel_seconds = model.profiles[0].edges[0].duration
            entries, _, _ = solver._build_route_schedule_entries(
                [customer],
                DEPOT,
                vehicle,
                DEPOT,
                start_time_seconds=840 * 60 - model_travel_seconds,
            )

        profile_durations = [edge.duration for edge in model.profiles[0].edges]
        self.assertEqual([88, 88 + 61], profile_durations)
        self.assertEqual(840 * 60, entries[0]["arrival_time_seconds"])

        route = SimpleNamespace(
            vehicle_key="physical-van#1",
            vehicle_type=VehicleType.INTERNAL_BUS,
            vehicle_id=1,
            trip_number=1,
            customers=[customer],
            total_distance_km=1,
            total_time_minutes=1,
            planned_start_minutes=(840 * 60 - model_travel_seconds) / 60,
            planned_end_minutes=840,
            schedule_entries=entries,
            is_feasible=True,
        )
        solution = SimpleNamespace(routes=[route], dropped_customers=[])
        self.assertEqual(
            [],
            solver._validate_extracted_solution_constraints(solution),
        )

    def test_customer_window_uses_exact_seconds_and_keeps_boundary_inclusive(self):
        customer = make_customer(time_windows=[(600, 840)])
        vehicle = make_vehicle()
        solver = make_solver(
            customer=customer,
            vehicle=vehicle,
            config=make_config(time_windows=True),
        )
        entry = {
            "customer": customer,
            # Deliberately inconsistent legacy/UI minutes: exact seconds must
            # be authoritative for hard-constraint validation.
            "arrival_time_minutes": 900,
            "arrival_time_seconds": 840 * 60,
        }
        route = SimpleNamespace(
            vehicle_key="physical-van#1",
            vehicle_type=VehicleType.INTERNAL_BUS,
            vehicle_id=1,
            trip_number=1,
            customers=[customer],
            total_distance_km=1,
            total_time_minutes=1,
            planned_start_minutes=0,
            planned_end_minutes=1,
            schedule_entries=[entry],
            is_feasible=True,
        )
        solution = SimpleNamespace(
            routes=[route],
            dropped_customers=[],
        )

        self.assertEqual(
            [],
            solver._validate_extracted_solution_constraints(solution),
        )

        entry["arrival_time_seconds"] += 1
        violations = solver._validate_extracted_solution_constraints(solution)

        self.assertEqual(1, len(violations))
        self.assertIn("14:00:01", violations[0])
        self.assertIn("14:00:00", violations[0])
        self.assertIn("1.000", violations[0])

    def test_vroom_model_and_shared_report_use_the_same_travel_clock(self):
        customer = make_customer()
        vehicle = make_vehicle()
        solver = make_solver(
            VROOMSolver,
            customer=customer,
            vehicle=vehicle,
        )

        with (
            patch("vroom_solver.get_traffic_multiplier", return_value=1.45),
            patch("pyvrp_solver.get_traffic_multiplier", return_value=1.45),
        ):
            matrices = solver._profile_matrices(vehicle, [DEPOT, CLIENT])
            report_seconds = solver._travel_time_seconds(
                0,
                1,
                [DEPOT, CLIENT],
            )

        self.assertEqual(88, matrices["durations"][0][1])
        self.assertEqual(report_seconds, matrices["durations"][0][1])
        self.assertEqual(61, solver._minutes_to_seconds(1.001, "service"))

    def test_vrp_rust_matrix_matches_shared_travel_and_service_clock(self):
        customer = make_customer()
        vehicle = make_vehicle()
        solver = make_solver(
            VRPRustPrototype,
            customer=customer,
            vehicle=vehicle,
        )

        with (
            patch(
                "vrp_rust_prototype.get_traffic_multiplier",
                return_value=1.45,
            ),
            patch("pyvrp_solver.get_traffic_multiplier", return_value=1.45),
        ):
            matrix = solver._build_profile_matrix(
                "test-profile",
                vehicle,
                [DEPOT, CLIENT],
            )
            report_seconds = solver._travel_time_seconds(
                0,
                1,
                [DEPOT, CLIENT],
            )

        # Flat 2x2 matrix: depot->client is travel only; client->depot also
        # contains the client's source service duration.
        self.assertEqual([0, 88, 88 + 61, 0], matrix["travelTimes"])
        self.assertEqual(88, report_seconds)


if __name__ == "__main__":
    unittest.main()
