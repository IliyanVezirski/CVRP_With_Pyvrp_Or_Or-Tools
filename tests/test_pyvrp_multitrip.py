import copy
import unittest

from config import (
    CVRPConfig,
    LocationConfig,
    MainConfig,
    VehicleConfig,
    VehicleType,
    build_ordered_depots,
)
from input_handler import Customer, InputData
from main import (
    _solution_to_api_response,
    vehicle_config_to_worker_dict,
    vehicle_configs_from_worker_dicts,
)
from cvrp_solver import ORTOOLS_AVAILABLE
from osrm_client import DistanceMatrix
from pyvrp_solver import PYVRP_AVAILABLE, PyVRPSolver
from warehouse_manager import WarehouseAllocation, WarehouseManager


DEPOT = (42.0, 23.0)


def make_customers(count, volume=6):
    return [
        Customer(
            id=f"customer-{idx}",
            name=f"Customer {idx}",
            coordinates=(42.0, 23.0 + idx * 0.01),
            volume=volume,
            original_gps_data="",
        )
        for idx in range(1, count + 1)
    ]


def make_matrix(customers, depots=None):
    depots = list(depots or [DEPOT])
    size = len(depots) + len(customers)
    distances = [
        [0 if row == column else 1000 for column in range(size)]
        for row in range(size)
    ]
    durations = [
        [0 if row == column else 60 for column in range(size)]
        for row in range(size)
    ]
    locations = depots + [customer.coordinates for customer in customers]
    return DistanceMatrix(
        distances=distances,
        durations=durations,
        locations=locations,
        sources=list(range(size)),
        destinations=list(range(size)),
    )


def make_vehicle(**overrides):
    values = dict(
        vehicle_type=VehicleType.INTERNAL_BUS,
        capacity=10,
        count=1,
        config_id="physical-van",
        fixed_cost=0,
        max_time_hours=2,
        service_time_minutes=1,
        start_location=DEPOT,
        end_location=DEPOT,
        reload_location=DEPOT,
        reload_time_minutes=5,
    )
    values.update(overrides)
    return VehicleConfig(**values)


def make_config(enable_multiple_trips=True, **overrides):
    values = dict(
        enable_multiple_trips=enable_multiple_trips,
        time_limit_seconds=1,
        objective_metric="distance",
        allow_customer_skipping=False,
        enable_customer_time_windows=False,
        pyvrp_display_progress=False,
        pyvrp_num_neighbours=5,
        pyvrp_ils_no_improvement=100,
        pyvrp_ils_history_length=10,
        pyvrp_use_extended_operators=False,
        log_search=False,
    )
    values.update(overrides)
    return CVRPConfig(**values)


class MultiTripCoreConfigTests(unittest.TestCase):
    def test_reload_location_is_opt_in_for_ordered_matrix_depots(self):
        reload_location = (42.5, 23.5)
        vehicle = make_vehicle(reload_location=reload_location)

        self.assertNotIn(reload_location, build_ordered_depots(DEPOT, [vehicle]))
        self.assertIn(
            reload_location,
            build_ordered_depots(
                DEPOT,
                [vehicle],
                include_reload_locations=True,
            ),
        )

    def test_worker_roundtrip_preserves_reload_coordinate_tuple(self):
        original = make_vehicle(reload_location=(42.5, 23.5))
        payload = vehicle_config_to_worker_dict(original)
        payload["reload_location"] = list(payload["reload_location"])

        restored = vehicle_configs_from_worker_dicts([payload])[0]

        self.assertEqual(original.config_id, restored.config_id)
        self.assertEqual((42.5, 23.5), restored.reload_location)
        self.assertIsInstance(restored.reload_location, tuple)

    def test_warehouse_capacity_uses_shift_derived_trips_but_not_for_single_customer_cap(self):
        vehicle = make_vehicle(
            capacity=100,
            count=2,
            max_time_hours=1,
            service_time_minutes=10,
            reload_time_minutes=20,
        )
        main_config = MainConfig(
            vehicles=[vehicle],
            cvrp=CVRPConfig(enable_multiple_trips=True),
        )
        manager = WarehouseManager(main_config=main_config)

        # floor((60 + 20) / (10 + 20)) = two useful trips.
        self.assertEqual(400, manager._calculate_total_vehicle_capacity(10))
        self.assertEqual(100, manager._get_max_single_bus_capacity())

        single_trip_config = copy.deepcopy(main_config)
        single_trip_config.cvrp.enable_multiple_trips = False
        single_trip_manager = WarehouseManager(main_config=single_trip_config)
        self.assertEqual(200, single_trip_manager._calculate_total_vehicle_capacity(10))


@unittest.skipUnless(PYVRP_AVAILABLE, "PyVRP is not installed")
class PyVRPMultiTripTests(unittest.TestCase):
    def make_solver(self, customers, config=None, vehicle=None, depots=None):
        depots = list(depots or [DEPOT])
        return PyVRPSolver(
            config or make_config(),
            [vehicle or make_vehicle()],
            customers,
            make_matrix(customers, depots),
            depots,
            [],
            LocationConfig(depot_location=DEPOT),
        )

    def test_native_reloads_create_dynamic_courses_for_one_physical_vehicle(self):
        customers = make_customers(3)
        solver = self.make_solver(customers)
        model = solver._create_pyvrp_model()

        self.assertEqual(2, len(model.depots))
        self.assertEqual(0, model.depots[0].service_duration)
        self.assertEqual(5 * 60, model.depots[1].service_duration)
        self.assertEqual([1], model.vehicle_types[0].reload_depots)
        self.assertEqual(2, model.vehicle_types[0].max_reloads)

        result = solver.solve()

        self.assertTrue(result.is_feasible)
        self.assertEqual(1, result.total_vehicles_used)
        self.assertEqual(3, result.total_trips)
        self.assertEqual(2, result.second_trips_count)
        self.assertEqual([], result.dropped_customers)
        self.assertEqual([1, 2, 3], [route.trip_number for route in result.routes])
        self.assertTrue(all(route.trip_count == 3 for route in result.routes))
        self.assertEqual({"physical-van#1"}, {route.vehicle_key for route in result.routes})
        self.assertEqual(3, len({route.route_id for route in result.routes}))
        for previous, current in zip(result.routes, result.routes[1:]):
            self.assertAlmostEqual(
                previous.planned_end_minutes + 5,
                current.planned_start_minutes,
            )
        # Three 3-minute courses and two 5-minute reloads.
        self.assertAlmostEqual(19, result.total_time_minutes)

        allocation = WarehouseAllocation(
            vehicle_customers=customers,
            warehouse_customers=[],
            total_vehicle_capacity=30,
            total_vehicle_volume=18,
            warehouse_volume=0,
            capacity_utilization=0.6,
        )
        # API output still needs a stable, distinct course identity when a
        # backend does not provide route_id.
        for route in result.routes:
            route.route_id = ""
        api_result = _solution_to_api_response(
            result,
            InputData(customers, 18, DEPOT),
            allocation,
            {},
            1.0,
        )
        self.assertEqual(3, api_result["total_trips"])
        self.assertEqual(2, api_result["second_trips_count"])
        self.assertEqual("physical-van#1", api_result["routes"][1]["vehicle_key"])
        self.assertEqual(2, api_result["routes"][1]["trip_number"])
        self.assertEqual(["1", "2", "3"], [route["route_id"] for route in api_result["routes"]])
        self.assertEqual(
            ["1004501001", "1004501002", "1004501003"],
            [route["bus_number"] for route in api_result["routes"]],
        )
        self.assertEqual("08:08", api_result["routes"][1]["planned_start"])

    def test_time_objective_includes_reload_duration_in_fitness(self):
        customers = make_customers(3)
        solver = self.make_solver(
            customers,
            config=make_config(
                objective_metric="time",
                time_objective_include_waiting=False,
            ),
        )

        result = solver.solve()

        # Each one-customer course costs three minutes and the two reloads
        # cost another five minutes each: 9 + 10 = 19 minutes = 1140 sec.
        self.assertEqual(3, result.total_trips)
        self.assertAlmostEqual(19 * 60, result.fitness_score)

    def test_full_workday_time_objective_prices_only_time(self):
        customer = make_customers(1)[0]
        customer.time_windows = [(10, 60)]
        solver = self.make_solver(
            [customer],
            config=make_config(
                enable_multiple_trips=False,
                objective_metric="time",
                time_objective_include_waiting=True,
                enable_customer_time_windows=True,
            ),
            vehicle=make_vehicle(start_time_minutes=0),
        )

        result = solver.solve()

        # Start 00:00, arrive 00:01, wait until 00:10, serve one minute and
        # return one minute: fitness is exactly 12 minutes in seconds. The two
        # 1000 m arcs remain available for reporting but have zero cost.
        self.assertAlmostEqual(12, result.total_time_minutes)
        self.assertEqual(12 * 60, result.fitness_score)

    def test_global_disable_keeps_single_trip_capacity(self):
        customers = make_customers(2)
        solver = self.make_solver(
            customers,
            config=make_config(
                enable_multiple_trips=False,
                allow_customer_skipping=True,
            ),
        )
        model = solver._create_pyvrp_model()

        self.assertEqual(1, len(model.depots))
        self.assertEqual([], model.vehicle_types[0].reload_depots)

        result = solver.solve()
        self.assertEqual(1, result.total_trips)
        self.assertEqual(0, result.second_trips_count)
        self.assertEqual(1, len(result.dropped_customers))

    def test_mandatory_customer_is_required_while_other_customers_remain_optional(self):
        customers = make_customers(2)
        customers[1].mandatory = True
        solver = self.make_solver(
            customers,
            config=make_config(
                enable_multiple_trips=False,
                allow_customer_skipping=True,
            ),
        )

        result = solver.solve()

        served_ids = [customer.id for route in result.routes for customer in route.customers]
        self.assertIn(customers[1].id, served_ids)
        self.assertNotIn(customers[1].id, [customer.id for customer in result.dropped_customers])

    def test_duplicate_business_id_tracks_mandatory_and_optional_visits_separately(self):
        customers = make_customers(2)
        customers[0].id = "shared-business-id"
        customers[1].id = "shared-business-id"
        customers[1].mandatory = True
        solver = self.make_solver(
            customers,
            config=make_config(
                enable_multiple_trips=False,
                allow_customer_skipping=True,
            ),
        )

        result = solver.solve()

        served = [customer for route in result.routes for customer in route.customers]
        self.assertEqual(1, len(served))
        self.assertIs(customers[1], served[0])
        self.assertEqual(1, len(result.dropped_customers))
        self.assertIs(customers[0], result.dropped_customers[0])
        self.assertTrue(result.is_feasible)

    def test_mandatory_audit_cannot_be_masked_by_served_duplicate_business_id(self):
        customers = make_customers(2)
        customers[0].id = "shared-business-id"
        customers[1].id = "shared-business-id"
        customers[1].mandatory = True
        solver = self.make_solver(
            customers,
            config=make_config(
                enable_multiple_trips=False,
                allow_customer_skipping=True,
            ),
        )
        result = solver.solve()

        # Simulate a malformed extraction in which the optional duplicate is
        # reported as served and the dropped list was masked by the shared ID.
        result.routes[0].customers = [customers[0]]
        result.dropped_customers = []

        violations = solver._validate_extracted_solution_constraints(result)

        self.assertTrue(
            any("абсолютно задължителни" in violation for violation in violations)
        )

    def test_separate_reload_location_connects_courses_and_preserves_fixed_end(self):
        reload_location = (42.1, 23.1)
        fixed_end = (42.2, 23.2)
        customers = make_customers(2)
        vehicle = make_vehicle(
            reload_location=reload_location,
            end_location=fixed_end,
        )
        depots = build_ordered_depots(
            DEPOT,
            [vehicle],
            include_reload_locations=True,
        )
        solver = self.make_solver(customers, vehicle=vehicle, depots=depots)

        result = solver.solve()

        self.assertTrue(result.is_feasible)
        self.assertEqual(2, result.total_trips)
        self.assertEqual(reload_location, result.routes[0].end_location)
        self.assertEqual(reload_location, result.routes[1].depot_location)
        self.assertEqual(fixed_end, result.routes[1].end_location)
        self.assertAlmostEqual(
            result.routes[0].planned_end_minutes + 5,
            result.routes[1].planned_start_minutes,
        )

    def test_reload_and_final_return_must_fit_inside_the_daily_shift(self):
        customers = make_customers(2)
        vehicle = make_vehicle(max_time_hours=0.15)  # 9 minutes
        solver = self.make_solver(
            customers,
            config=make_config(allow_customer_skipping=True),
            vehicle=vehicle,
        )

        result = solver.solve()

        # One course is 3 minutes. A second course would require another
        # 3 minutes plus the 5-minute reload, which does not fit in 9 minutes.
        self.assertEqual(1, result.total_trips)
        self.assertEqual(1, len(result.dropped_customers))
        self.assertLessEqual(result.total_time_minutes, 9)

    def test_work_time_audit_allows_exactly_one_minute_rounding_tolerance(self):
        customers = make_customers(1, volume=1)
        vehicle = make_vehicle(max_time_hours=8)
        solver = self.make_solver(
            customers,
            config=make_config(enable_multiple_trips=False),
            vehicle=vehicle,
        )
        result = solver.solve()
        route = result.routes[0]
        route.planned_start_minutes = 0

        route.planned_end_minutes = 481.0
        violations = solver._validate_extracted_solution_constraints(result)
        self.assertFalse(any("работно време" in item for item in violations))

        route.planned_end_minutes = 481.01
        violations = solver._validate_extracted_solution_constraints(result)
        self.assertTrue(any("работно време" in item for item in violations))

    def test_customer_limit_is_cumulative_across_the_day(self):
        customers = make_customers(3, volume=1)
        vehicle = make_vehicle(capacity=10, max_customers_per_day=2)
        solver = self.make_solver(
            customers,
            config=make_config(allow_customer_skipping=True),
            vehicle=vehicle,
        )

        result = solver.solve()

        self.assertEqual(2, sum(len(route.customers) for route in result.routes))
        self.assertEqual(1, len(result.dropped_customers))
        self.assertEqual(1, result.total_vehicles_used)

    def test_daily_customer_limit_also_applies_when_multi_trip_is_disabled(self):
        customers = make_customers(3, volume=1)
        vehicle = make_vehicle(capacity=10, max_customers_per_day=2)
        solver = self.make_solver(
            customers,
            config=make_config(
                enable_multiple_trips=False,
                allow_customer_skipping=True,
            ),
            vehicle=vehicle,
        )

        result = solver.solve()

        self.assertEqual(1, result.total_trips)
        self.assertEqual(2, sum(len(route.customers) for route in result.routes))
        self.assertEqual(1, len(result.dropped_customers))

    def test_legacy_customer_limit_is_a_daily_fallback_in_multi_trip_mode(self):
        customers = make_customers(3, volume=1)
        vehicle = make_vehicle(
            capacity=10,
            max_customers_per_day=None,
            max_customers_per_route=2,
        )
        solver = self.make_solver(
            customers,
            config=make_config(allow_customer_skipping=True),
            vehicle=vehicle,
        )

        result = solver.solve()

        self.assertEqual(2, sum(len(route.customers) for route in result.routes))
        self.assertEqual(1, len(result.dropped_customers))

    @unittest.skipUnless(ORTOOLS_AVAILABLE, "OR-Tools exact fallback is unavailable")
    def test_daily_limit_uses_other_available_buses_instead_of_clipping(self):
        customers = make_customers(4, volume=1)
        vehicle = make_vehicle(
            capacity=10,
            count=2,
            max_customers_per_day=2,
        )
        solver = self.make_solver(customers, vehicle=vehicle)

        result = solver.solve()

        served_by_vehicle = {}
        for route in result.routes:
            served_by_vehicle.setdefault(route.vehicle_key, 0)
            served_by_vehicle[route.vehicle_key] += len(route.customers)
        self.assertTrue(result.is_feasible)
        self.assertEqual([], result.dropped_customers)
        self.assertEqual(2, result.total_vehicles_used)
        self.assertEqual(4, sum(served_by_vehicle.values()))
        self.assertTrue(all(count <= 2 for count in served_by_vehicle.values()))

    @unittest.skipUnless(ORTOOLS_AVAILABLE, "OR-Tools exact fallback is unavailable")
    def test_time_objective_real_km_limit_uses_all_feasible_buses(self):
        customers = make_customers(4, volume=1)
        vehicle = make_vehicle(
            capacity=10,
            count=4,
            max_distance_km=2,
        )
        solver = self.make_solver(
            customers,
            config=make_config(objective_metric="time"),
            vehicle=vehicle,
        )

        result = solver.solve()

        self.assertTrue(result.is_feasible)
        self.assertEqual([], result.dropped_customers)
        self.assertEqual(4, result.total_vehicles_used)
        self.assertTrue(all(route.total_distance_km <= 2 for route in result.routes))

    def test_missing_config_ids_do_not_merge_duplicate_vehicle_rows(self):
        customers = make_customers(1, volume=1)
        first = make_vehicle(config_id="")
        second = make_vehicle(config_id="")
        solver = PyVRPSolver(
            make_config(),
            [first, second],
            customers,
            make_matrix(customers),
            [DEPOT],
            [],
            LocationConfig(depot_location=DEPOT),
        )

        first_key = solver._vehicle_key(first, 0, 1)
        second_key = solver._vehicle_key(second, 1, 1)
        self.assertNotEqual(first_key, second_key)


if __name__ == "__main__":
    unittest.main()
