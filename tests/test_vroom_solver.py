import math
import unittest
from unittest.mock import patch

from config import (
    CVRPConfig,
    CenterZoneConfig,
    LocationConfig,
    VehicleConfig,
    VehicleType,
)
from input_handler import Customer
from osrm_client import DistanceMatrix
from vroom_solver import (
    VROOMSolverError,
    VROOMUnsupportedFeatureError,
    solve_cvrp_vroom,
)
from warehouse_manager import WarehouseAllocation


DEPOT_A = (42.0000, 23.0000)
DEPOT_B = (42.0200, 23.0200)


def make_customers():
    first = Customer(
        id="client-a",
        name="Client A",
        coordinates=(42.0100, 23.0100),
        volume=1.25,
        original_gps_data="",
        time_windows=[(480, 600), (960, 1080)],
    )
    # Keep the test compatible both before and after the HTTP input model gains
    # this optional dataclass field.
    first.service_time_minutes = 4

    second = Customer(
        id="client-b",
        name="Client B",
        coordinates=(42.0300, 23.0300),
        volume=2.0,
        original_gps_data="",
        time_windows=[(480, 1200)],
    )
    return [first, second]


def make_vehicles():
    return [
        VehicleConfig(
            vehicle_type=VehicleType.INTERNAL_BUS,
            capacity=12.5,
            count=1,
            config_id="internal-config",
            name="Internal",
            fixed_cost=1000,
            max_distance_km=50,
            max_time_hours=4,
            service_time_minutes=7,
            start_location=DEPOT_A,
            end_location=DEPOT_B,
            max_customers_per_day=3,
            start_time_minutes=480,
        ),
        VehicleConfig(
            vehicle_type=VehicleType.CENTER_BUS,
            capacity=20,
            count=1,
            config_id="center-config",
            name="Center",
            fixed_cost=0,
            max_distance_km=None,
            max_time_hours=5,
            service_time_minutes=11,
            start_location=DEPOT_B,
            end_location=DEPOT_A,
            start_time_minutes=480,
        ),
    ]


def make_config(**overrides):
    values = dict(
        solver_type="vroom",
        enable_multiple_trips=False,
        objective_metric="time",
        allow_customer_skipping=True,
        enable_customer_time_windows=True,
        enable_priority_dropping=False,
        time_limit_seconds=5,
    )
    values.update(overrides)
    return CVRPConfig(**values)


def make_location_config(customers):
    zone = CenterZoneConfig(
        name="Internal discount",
        mode="circle",
        center_coords=customers[0].coordinates,
        radius_km=0.5,
        enable_priority=True,
        enable_restrictions=False,
        priority_vehicle_types=[VehicleType.INTERNAL_BUS.value],
        restricted_vehicle_types=[],
        discount_priority_vehicle=0.5,
        priority_vehicle_outside_penalty=0,
        vehicle_penalties={},
    )
    return LocationConfig(
        depot_location=DEPOT_A,
        center_location=DEPOT_A,
        center_zone_mode="circle",
        center_zone_polygon=[],
        center_zone_radius_km=0,
        enable_center_zone_priority=False,
        enable_center_zone_restrictions=False,
        center_zones=[zone],
        city_center_coords=DEPOT_A,
        city_traffic_radius_km=20,
        city_traffic_duration_multiplier=2.0,
        enable_city_traffic_adjustment=True,
    )


def make_matrix(customers):
    locations = [DEPOT_A, DEPOT_B] + [customer.coordinates for customer in customers]
    size = len(locations)
    return DistanceMatrix(
        distances=[
            [0 if row == column else 1000 for column in range(size)]
            for row in range(size)
        ],
        durations=[
            [0 if row == column else 60 for column in range(size)]
            for row in range(size)
        ],
        locations=locations,
        sources=list(range(size)),
        destinations=list(range(size)),
    )


def make_allocation(customers):
    return WarehouseAllocation(
        vehicle_customers=customers,
        warehouse_customers=[],
        total_vehicle_capacity=100,
        total_vehicle_volume=sum(customer.volume for customer in customers),
        warehouse_volume=0,
        capacity_utilization=0,
        center_zone_customers=[customers[0]],
    )


def solved_response(*job_ids, cost=987654, unassigned=None):
    steps = [{"type": "start"}]
    steps.extend({"type": "job", "id": job_id} for job_id in job_ids)
    steps.append({"type": "end"})
    return {
        "code": 0,
        "summary": {"cost": cost},
        "routes": [
            {
                "vehicle": 1,
                "cost": cost,
                "violations": [],
                "steps": steps,
            }
        ],
        "unassigned": list(unassigned or []),
    }


class VROOMSolverTests(unittest.TestCase):
    def setUp(self):
        self.customers = make_customers()
        self.vehicles = make_vehicles()
        self.matrix = make_matrix(self.customers)
        self.locations = make_location_config(self.customers)
        self.allocation = make_allocation(self.customers)

    def solve_with_response(self, config, response):
        captured = {}

        def fake_runtime(payload, runtime_config):
            captured["payload"] = payload
            captured["config"] = runtime_config
            return response, "1.15.0-test"

        with patch("vroom_solver.run_vroom_problem", side_effect=fake_runtime) as runtime:
            solution = solve_cvrp_vroom(
                self.allocation,
                DEPOT_A,
                self.matrix,
                config=config,
                location_config=self.locations,
                vehicle_configs=self.vehicles,
            )
        self.assertEqual(1, runtime.call_count)
        return solution, captured["payload"]

    def test_official_payload_preserves_constraints_profiles_and_service(self):
        solution, payload = self.solve_with_response(
            make_config(),
            solved_response(1, 2),
        )

        self.assertEqual({"jobs", "vehicles", "matrices"}, set(payload))
        self.assertEqual(2, len(payload["jobs"]))
        self.assertEqual(2, len(payload["vehicles"]))
        self.assertEqual(2, len(payload["matrices"]))

        internal = payload["vehicles"][0]
        center = payload["vehicles"][1]
        self.assertEqual(0, internal["start_index"])
        self.assertEqual(1, internal["end_index"])
        self.assertEqual([23.0, 42.0], internal["start"])
        self.assertEqual([1250, 3], internal["capacity"])
        self.assertEqual(3, internal["max_tasks"])
        self.assertEqual(50_000, internal["max_distance"])
        self.assertEqual([28_800, 43_200], internal["time_window"])
        self.assertEqual(14_400, internal["max_travel_time"])
        self.assertEqual({"fixed": 90, "per_task_hour": 3600}, internal["costs"])
        self.assertEqual(1, center["start_index"])
        self.assertEqual(0, center["end_index"])
        self.assertEqual([2000, 2], center["capacity"])

        overridden = payload["jobs"][0]
        per_vehicle = payload["jobs"][1]
        self.assertEqual([125, 1], overridden["delivery"])
        self.assertEqual(240, overridden["service"])
        self.assertNotIn("service_per_type", overridden)
        self.assertEqual([[28_800, 36_000], [57_600, 64_800]], overridden["time_windows"])
        self.assertEqual([23.01, 42.01], overridden["location"])
        self.assertEqual(0, per_vehicle["service"])
        self.assertEqual(
            {"vroom_type_1": 420, "vroom_type_2": 660},
            per_vehicle["service_per_type"],
        )
        self.assertTrue(all(job["priority"] == 100 for job in payload["jobs"]))

        internal_matrix = payload["matrices"][internal["profile"]]
        center_matrix = payload["matrices"][center["profile"]]
        customer_a_index = 2
        self.assertEqual(120, internal_matrix["durations"][0][customer_a_index])
        self.assertEqual(1000, internal_matrix["distances"][0][customer_a_index])
        self.assertEqual(60, internal_matrix["costs"][0][customer_a_index])
        self.assertEqual(120, center_matrix["costs"][0][customer_a_index])

        self.assertTrue(solution.is_feasible)
        self.assertEqual(["client-a", "client-b"], [c.id for c in solution.routes[0].customers])
        self.assertEqual(DEPOT_A, solution.routes[0].depot_location)
        self.assertEqual(DEPOT_B, solution.routes[0].end_location)
        self.assertEqual("internal-config#1:trip:1", solution.routes[0].route_id)
        self.assertEqual([], solution.dropped_customers)
        self.assertEqual("vroom", solution.solver_backend)
        self.assertEqual("1.15.0-test", solution.solver_version)
        self.assertAlmostEqual(solution.total_time_minutes * 60, solution.fitness_score)
        self.assertNotEqual(987654, solution.fitness_score)

    def test_distance_mode_uses_custom_distance_cost_and_vroom_summary_cost(self):
        solution, payload = self.solve_with_response(
            make_config(objective_metric="distance"),
            solved_response(1, 2, cost=4321),
        )

        internal = payload["vehicles"][0]
        matrix = payload["matrices"][internal["profile"]]
        self.assertEqual(120, matrix["durations"][0][2])
        self.assertEqual(1000, matrix["distances"][0][2])
        self.assertEqual(500, matrix["costs"][0][2])
        self.assertEqual({"fixed": 1000}, internal["costs"])
        self.assertEqual(4321, solution.fitness_score)
        self.assertTrue(solution.is_feasible)

    def test_drop_penalties_are_normalized_to_vroom_priority_range(self):
        _, payload = self.solve_with_response(
            make_config(
                enable_priority_dropping=True,
                min_customer_drop_penalty=10_000,
                max_customer_drop_penalty=100_000,
            ),
            solved_response(1, 2),
        )

        priorities = [job["priority"] for job in payload["jobs"]]
        self.assertEqual([1, 100], sorted(priorities))
        self.assertTrue(all(1 <= priority <= 100 for priority in priorities))

    def test_required_unassigned_customer_is_rejected_by_independent_audit(self):
        response = solved_response(
            1,
            unassigned=[{"id": 2, "type": "job"}],
        )
        solution, payload = self.solve_with_response(
            make_config(allow_customer_skipping=False),
            response,
        )

        self.assertTrue(all(job["priority"] == 100 for job in payload["jobs"]))
        self.assertEqual(["client-b"], [customer.id for customer in solution.dropped_customers])
        self.assertFalse(solution.is_feasible)
        self.assertTrue(math.isinf(solution.fitness_score))
        self.assertFalse(solution.routes[0].is_feasible)

    def test_per_customer_mandatory_job_is_prioritized_and_cannot_be_accepted_unassigned(self):
        self.customers[1].mandatory = True
        response = solved_response(
            1,
            unassigned=[{"id": 2, "type": "job"}],
        )

        solution, payload = self.solve_with_response(make_config(allow_customer_skipping=True), response)

        self.assertEqual(100, payload["jobs"][1]["priority"])
        self.assertLessEqual(payload["jobs"][0]["priority"], 99)
        self.assertFalse(solution.is_feasible)
        self.assertTrue(math.isinf(solution.fitness_score))

    def test_multiple_trips_fail_before_runtime_call(self):
        with patch("vroom_solver.run_vroom_problem") as runtime:
            with self.assertRaises(VROOMUnsupportedFeatureError):
                solve_cvrp_vroom(
                    self.allocation,
                    DEPOT_A,
                    self.matrix,
                    config=make_config(enable_multiple_trips=True),
                    location_config=self.locations,
                    vehicle_configs=self.vehicles,
                )
        runtime.assert_not_called()

    def test_malformed_matrix_fails_before_runtime_call(self):
        malformed = DistanceMatrix(
            distances=[[0]],
            durations=[[0]],
            locations=[DEPOT_A],
            sources=[0],
            destinations=[0],
        )
        with patch("vroom_solver.run_vroom_problem") as runtime:
            with self.assertRaisesRegex(VROOMSolverError, "expected 4"):
                solve_cvrp_vroom(
                    self.allocation,
                    DEPOT_A,
                    malformed,
                    config=make_config(),
                    location_config=self.locations,
                    vehicle_configs=self.vehicles,
                )
        runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
