import unittest

from config import CVRPConfig, VehicleConfig, VehicleType
from cvrp_solver import ORTOOLS_AVAILABLE, ORToolsSolver
from input_handler import Customer
from osrm_client import DistanceMatrix


START_DEPOT = (42.0, 23.0)
FIXED_END = (42.5, 23.5)
RELOAD_DEPOT = (42.25, 23.25)


def make_customers(count):
    return [
        Customer(
            id=f"customer-{index}",
            name=f"Customer {index}",
            coordinates=(42.0 + index * 0.01, 23.1),
            volume=6,
            original_gps_data="",
        )
        for index in range(1, count + 1)
    ]


def make_matrix(depots, customers):
    size = len(depots) + len(customers)
    distances = [
        [0 if row == column else 1000 for column in range(size)]
        for row in range(size)
    ]
    durations = [
        [0 if row == column else 600 for column in range(size)]
        for row in range(size)
    ]
    locations = list(depots) + [customer.coordinates for customer in customers]
    return DistanceMatrix(
        distances=distances,
        durations=durations,
        locations=locations,
        sources=list(range(size)),
        destinations=list(range(size)),
    )


def make_config(enable_multiple_trips=True, **overrides):
    values = dict(
        enable_multiple_trips=enable_multiple_trips,
        time_limit_seconds=2,
        objective_metric="distance",
        allow_customer_skipping=False,
        enable_priority_dropping=False,
        distance_penalty_disjunction=100_000_000,
        enable_final_depot_reconfiguration=False,
        first_solution_strategy="PATH_CHEAPEST_ARC",
        local_search_metaheuristic="AUTOMATIC",
        log_search=False,
    )
    values.update(overrides)
    return CVRPConfig(**values)


def make_vehicle(**overrides):
    values = dict(
        vehicle_type=VehicleType.INTERNAL_BUS,
        capacity=10,
        count=1,
        config_id="physical-van",
        fixed_cost=0,
        max_time_hours=8,
        service_time_minutes=5,
        start_location=START_DEPOT,
        end_location=FIXED_END,
        reload_location=START_DEPOT,
        reload_time_minutes=30,
        max_customers_per_day=20,
    )
    values.update(overrides)
    return VehicleConfig(**values)


@unittest.skipUnless(ORTOOLS_AVAILABLE, "OR-Tools is not installed")
class ORToolsMultiTripTests(unittest.TestCase):
    def make_solver(self, customer_count, config=None, vehicle=None):
        customers = make_customers(customer_count)
        depots = [START_DEPOT, FIXED_END]
        solver = ORToolsSolver(
            config or make_config(),
            [vehicle or make_vehicle()],
            customers,
            make_matrix(depots, customers),
            depots,
        )
        return solver

    def test_full_workday_time_objective_prices_waiting(self):
        customers = make_customers(1)
        customers[0].time_windows = [(60, 120)]
        depots = [START_DEPOT, FIXED_END]
        vehicle = make_vehicle(
            capacity=10,
            count=1,
            start_time_minutes=0,
        )
        solver = ORToolsSolver(
            make_config(
                enable_multiple_trips=False,
                objective_metric="time",
                time_objective_include_waiting=True,
                enable_customer_time_windows=True,
            ),
            [vehicle],
            customers,
            make_matrix(depots, customers),
            depots,
        )

        result = solver.solve()

        # 10 min to the client, 50 min waiting, 5 min service and 10 min
        # to the fixed end = fitness of exactly 75 minutes in seconds. Distance
        # remains available for hard limits/reporting and has zero cost.
        self.assertAlmostEqual(75, result.total_time_minutes)
        self.assertEqual(75 * 60, result.fitness_score)

    def test_dynamic_reload_nodes_create_as_many_courses_as_resources_allow(self):
        solver = self.make_solver(4)
        data = solver._create_data_model()

        self.assertEqual(3, len(data["reload_nodes"]))
        self.assertTrue(all(kind == "reload" for kind in data["node_kinds"][-3:]))
        self.assertEqual([2, 3, 4, 5], data["customer_node_indices"])

        result = solver.solve()

        self.assertEqual(4, result.total_trips)
        self.assertEqual(3, result.second_trips_count)
        self.assertEqual(1, result.total_vehicles_used)
        self.assertEqual([], result.dropped_customers)
        self.assertEqual([1, 2, 3, 4], [route.trip_number for route in result.routes])
        self.assertTrue(all(route.trip_count == 4 for route in result.routes))
        self.assertEqual(1, len({route.vehicle_key for route in result.routes}))
        self.assertEqual(4, len({route.route_id for route in result.routes}))
        self.assertTrue(all(route.total_volume <= 10 for route in result.routes))

        for route in result.routes[:-1]:
            self.assertEqual(START_DEPOT, route.end_location)
        self.assertEqual(FIXED_END, result.routes[-1].end_location)
        for previous, current in zip(result.routes, result.routes[1:]):
            self.assertAlmostEqual(
                previous.planned_end_minutes + 30,
                current.planned_start_minutes,
            )
        self.assertAlmostEqual(480, result.routes[0].planned_start_minutes)
        # Four 25-minute courses plus three 30-minute reloads.
        self.assertAlmostEqual(190, result.total_time_minutes)

    def test_global_disable_preserves_single_trip_capacity_behavior(self):
        config = make_config(
            enable_multiple_trips=False,
            allow_customer_skipping=True,
        )
        solver = self.make_solver(2, config=config)
        data = solver._create_data_model()
        self.assertEqual({}, data["reload_nodes"])

        result = solver.solve()

        self.assertEqual(1, result.total_trips)
        self.assertEqual(0, result.second_trips_count)
        self.assertEqual(1, len(result.dropped_customers))
        self.assertEqual(1, len(result.routes[0].customers))

    def test_mandatory_customer_cannot_be_dropped_when_optional_skipping_is_enabled(self):
        config = make_config(
            enable_multiple_trips=False,
            allow_customer_skipping=True,
        )
        customers = make_customers(2)
        customers[1].mandatory = True
        depots = [START_DEPOT, FIXED_END]
        solver = ORToolsSolver(
            config,
            [make_vehicle()],
            customers,
            make_matrix(depots, customers),
            depots,
        )

        result = solver.solve()

        served_ids = [customer.id for route in result.routes for customer in route.customers]
        self.assertIn(customers[1].id, served_ids)
        self.assertNotIn(customers[1].id, [customer.id for customer in result.dropped_customers])

    def test_customer_limit_is_cumulative_for_the_physical_vehicle_day(self):
        config = make_config(allow_customer_skipping=True)
        vehicle = make_vehicle(max_customers_per_day=2)
        solver = self.make_solver(3, config=config, vehicle=vehicle)
        data = solver._create_data_model()

        self.assertEqual([2], data["vehicle_max_stops"])
        self.assertEqual(1, len(data["reload_nodes"]))

        result = solver.solve()
        served = sum(len(route.customers) for route in result.routes)

        self.assertEqual(2, served)
        self.assertEqual(1, len(result.dropped_customers))
        self.assertEqual(2, result.total_trips)

    def test_daily_customer_limit_also_applies_when_multi_trip_is_disabled(self):
        config = make_config(
            enable_multiple_trips=False,
            allow_customer_skipping=True,
        )
        vehicle = make_vehicle(capacity=100, max_customers_per_day=2)
        solver = self.make_solver(3, config=config, vehicle=vehicle)

        result = solver.solve()

        self.assertEqual(1, result.total_trips)
        self.assertEqual(2, sum(len(route.customers) for route in result.routes))
        self.assertEqual(1, len(result.dropped_customers))

    def test_reload_time_counts_against_the_cumulative_work_time(self):
        config = make_config(allow_customer_skipping=True)
        vehicle = make_vehicle(max_time_hours=1.5)
        solver = self.make_solver(3, config=config, vehicle=vehicle)

        result = solver.solve()

        self.assertEqual(2, result.total_trips)
        self.assertEqual(1, len(result.dropped_customers))
        self.assertLessEqual(result.total_time_minutes, 90)
        self.assertAlmostEqual(80, result.total_time_minutes)

    def test_distance_limit_is_cumulative_across_courses(self):
        config = make_config(allow_customer_skipping=True)
        vehicle = make_vehicle(max_distance_km=4)
        solver = self.make_solver(3, config=config, vehicle=vehicle)

        result = solver.solve()

        self.assertEqual(2, result.total_trips)
        self.assertEqual(1, len(result.dropped_customers))
        self.assertLessEqual(result.total_distance_km, 4)
        self.assertAlmostEqual(4, result.total_distance_km)

    def test_distinct_reload_location_splits_courses_without_changing_fixed_end(self):
        customers = make_customers(2)
        depots = [START_DEPOT, RELOAD_DEPOT, FIXED_END]
        vehicle = make_vehicle(reload_location=RELOAD_DEPOT)
        solver = ORToolsSolver(
            make_config(),
            [vehicle],
            customers,
            make_matrix(depots, customers),
            depots,
        )

        data = solver._create_data_model()
        reload_meta = next(iter(data["reload_nodes"].values()))
        self.assertEqual(RELOAD_DEPOT, reload_meta["location"])

        result = solver.solve()

        self.assertEqual(2, result.total_trips)
        self.assertEqual(START_DEPOT, result.routes[0].depot_location)
        self.assertEqual(RELOAD_DEPOT, result.routes[0].end_location)
        self.assertEqual(RELOAD_DEPOT, result.routes[1].depot_location)
        self.assertEqual(FIXED_END, result.routes[1].end_location)


if __name__ == "__main__":
    unittest.main()
