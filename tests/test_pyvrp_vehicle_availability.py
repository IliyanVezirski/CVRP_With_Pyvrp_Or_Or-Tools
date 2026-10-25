"""Zero-count fleet rows and original solver errors; synthetic data only."""
import pickle
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pyvrp_next_runtime as runtime
import pyvrp_next_worker as worker
import pyvrp_solver as adapter
from config import LocationConfig, VehicleType
from tests.test_pyvrp_multitrip import DEPOT, make_config, make_customers, make_matrix, make_vehicle


SECOND_DEPOT = (43.0, 23.5)
UNUSED_DEPOT = (44.0, 23.5)


def make_solver(vehicles):
    customers = make_customers(1, volume=1)
    customers[0].mandatory = True
    depots = [DEPOT, SECOND_DEPOT, UNUSED_DEPOT]
    return adapter.PyVRPSolver(
        make_config(enable_multiple_trips=False), vehicles, customers,
        make_matrix(customers, depots), depots,
        location_config=LocationConfig(depot_location=DEPOT),
    )


class VehicleAvailabilityTests(unittest.TestCase):
    def fleet(self):
        return [
            make_vehicle(config_id="first", name="First", count=2, capacity=13),
            make_vehicle(config_id="zero", name="Zero", count=0, enabled=True,
                         vehicle_type=VehicleType.VRATZA_BUS, start_location=UNUSED_DEPOT,
                         end_location=UNUSED_DEPOT, service_time_minutes=90),
            make_vehicle(config_id="disabled", count=7, enabled=False),
            make_vehicle(config_id="second", name="Second", count=1, capacity=17,
                         vehicle_type=VehicleType.CENTER_BUS, start_location=SECOND_DEPOT,
                         end_location=SECOND_DEPOT, reload_location=SECOND_DEPOT),
        ]

    def test_available_type_indexes_ignore_zero_and_disabled_rows_without_mutation(self):
        vehicles = self.fleet()
        original = deepcopy(vehicles)
        solver = make_solver(vehicles)
        self.assertEqual(solver._available_vehicle_configs(), [vehicles[0], vehicles[3]])
        self.assertIs(solver._get_vehicle_config_for_type_index(0), vehicles[0])
        self.assertIs(solver._get_vehicle_config_for_type_index(1), vehicles[3])
        self.assertIsNone(solver._get_vehicle_config_for_type_index(2))
        self.assertIs(solver._get_vehicle_config_for_type(VehicleType.CENTER_BUS), vehicles[3])
        self.assertEqual(vehicles, original)
        self.assertEqual(solver.unique_depots, [DEPOT, SECOND_DEPOT, UNUSED_DEPOT])

    @unittest.skipUnless(adapter.PYVRP_AVAILABLE, "Native PyVRP is not installed")
    def test_native_model_keeps_profiles_counts_and_depots_aligned(self):
        for zero_position in (0, 1, 2):
            for multi_trip in (False, True):
                with self.subTest(zero_position=zero_position, multi_trip=multi_trip):
                    first, zero, disabled, second = self.fleet()
                    vehicles = [first, second]
                    vehicles.insert(zero_position, zero)
                    vehicles.insert(1, disabled)
                    solver = make_solver(vehicles)
                    solver.config.enable_multiple_trips = multi_trip
                    model = solver._create_pyvrp_model()
                    types = model.data().vehicle_types()
                    self.assertEqual(len(types), 2)
                    self.assertEqual([item.num_available for item in types], [2, 1])
                    self.assertEqual([item.start_depot for item in types], [0, 1])
                    self.assertEqual([item.end_depot for item in types], [0, 1])
                    self.assertEqual([item.capacity[0] for item in types], [1300, 1700])
                    self.assertIs(solver._get_vehicle_config_for_type_index(0), first)
                    self.assertIs(solver._get_vehicle_config_for_type_index(1), second)
                    self.assertEqual(len(model.depots), 3 + (2 if multi_trip else 0))

    @unittest.skipUnless(adapter.PYVRP_AVAILABLE, "Native PyVRP is not installed")
    def test_native_solution_uses_the_correct_vehicle_after_a_zero_row(self):
        first, zero, _, second = self.fleet()
        first.fixed_cost = 100000
        solver = make_solver([zero, first, second])
        solution = solver.solve()
        self.assertTrue(solution.is_feasible)
        self.assertEqual(solution.dropped_customers, [])
        self.assertEqual(len(solution.routes), 1)
        self.assertEqual(solution.routes[0].vehicle_name, "Second")
        self.assertEqual(solution.routes[0].depot_location, SECOND_DEPOT)
        self.assertEqual(solution.routes[0].end_location, SECOND_DEPOT)
        self.assertEqual(solution.routes[0].vehicle_type, VehicleType.CENTER_BUS)
        self.assertEqual(solution.routes[0].vehicle_key, "second#1")

    def test_no_available_vehicles_has_a_clear_error_not_dropped_customers(self):
        solver = make_solver([make_vehicle(count=0)])
        with (patch.object(adapter, "PYVRP_AVAILABLE", True),
              patch.object(solver, "_create_empty_solution") as empty,
              self.assertLogs("pyvrp_solver", level="ERROR"),
              self.assertRaisesRegex(ValueError, "Няма активни бусове с брой над 0")):
            solver.solve()
        empty.assert_not_called()

    def test_model_error_retains_original_exception(self):
        solver = make_solver([make_vehicle()])
        error = ValueError("num_available must be > 0.")
        with (patch.object(adapter, "PYVRP_AVAILABLE", True),
              patch.object(solver, "_create_pyvrp_model", side_effect=error),
              patch.object(solver, "_create_empty_solution") as empty,
              self.assertLogs("pyvrp_solver", level="ERROR"),
              self.assertRaises(ValueError) as caught):
            solver.solve()
        self.assertIs(caught.exception, error)
        empty.assert_not_called()

    def test_search_error_is_not_returned_as_an_empty_solution(self):
        solver = make_solver([make_vehicle()])
        model = SimpleNamespace(depots=[], clients=[], vehicle_types=[], data=lambda: object())
        error = RuntimeError("synthetic native search failure")
        with (patch.object(adapter, "PYVRP_AVAILABLE", True),
              patch.object(solver, "_create_pyvrp_model", return_value=model),
              patch.object(solver, "_create_solve_params", return_value=None),
              patch.object(adapter, "MaxRuntime", return_value=object()),
              patch.object(adapter, "solve", side_effect=error),
              patch.object(solver, "_create_empty_solution") as empty,
              self.assertLogs("pyvrp_solver", level="ERROR"),
              self.assertRaises(RuntimeError) as caught):
            solver.solve()
        self.assertIs(caught.exception, error)
        empty.assert_not_called()

    def test_missing_library_is_reported_directly(self):
        solver = make_solver([make_vehicle()])
        with (patch.object(adapter, "PYVRP_AVAILABLE", False),
              patch.object(solver, "_create_empty_solution") as empty,
              self.assertRaisesRegex(RuntimeError, "не е инсталиран")):
            solver.solve()
        empty.assert_not_called()

    def test_worker_and_parent_preserve_model_error_before_mandatory_validation(self):
        solver = make_solver([make_vehicle()])
        payload = {
            "allocation": SimpleNamespace(vehicle_customers=solver.customers, warehouse_customers=[]),
            "depot_location": DEPOT, "distance_matrix": solver.distance_matrix,
            "config": solver.config, "location_config": solver.location_config,
            "vehicle_configs": solver.vehicle_configs,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.pkl"
            with (patch.object(worker, "_pyvrp_version", return_value="0.14.0"),
                  patch.object(worker, "_read_request", return_value=payload),
                  patch.object(worker, "_configure_worker_logging"),
                  patch.object(adapter, "solve_cvrp_pyvrp", side_effect=ValueError("synthetic model error")),
                  patch.object(worker, "validate_mandatory_customer_solution") as validate):
                code = worker.run_worker(str(Path(directory) / "unused.pkl"), str(output))
            envelope = pickle.loads(output.read_bytes())
        self.assertEqual(code, 1)
        self.assertEqual(envelope["error_type"], "ValueError")
        self.assertEqual(envelope["error"], "synthetic model error")
        self.assertNotIn("missing 1", envelope["error"])
        validate.assert_not_called()

        config = SimpleNamespace(pyvrp_next_fallback_to_stable=False, time_limit_seconds=1)
        with (patch.object(runtime, "_resolve_worker_command", return_value=["fake-worker"]),
              patch.object(runtime, "_atomic_pickle_dump"),
              patch.object(runtime, "_run_process_streaming", return_value=SimpleNamespace(returncode=1, stdout="", stderr="")),
              patch.object(runtime.Path, "is_file", return_value=True),
              patch.object(runtime, "_pickle_load", return_value=envelope),
              patch.object(runtime, "_solve_with_stable_fallback") as fallback,
              self.assertRaisesRegex(runtime.PyVRPNextRuntimeError, "synthetic model error")):
            runtime.solve_cvrp_pyvrp_next(**{**payload, "config": config})
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
