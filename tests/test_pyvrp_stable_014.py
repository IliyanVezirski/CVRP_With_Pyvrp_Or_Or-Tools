"""Stable-release contract tests; tiny synthetic cases only, no external I/O."""

import contextlib
import io
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_exe
import build_pyvrp_next_worker as build_worker
import pyvrp_next_runtime as runtime
import pyvrp_next_worker as worker
import pyvrp_solver as adapter
import setup_pyvrp_next as setup
from pyvrp_next_version import PYVRP_NEXT_VERSION


class StableReleaseContractTests(unittest.TestCase):
    def test_exact_release_is_shared_by_setup_runtime_and_builds(self):
        self.assertEqual("0.14.0", PYVRP_NEXT_VERSION)
        for module in (setup, runtime, worker, build_worker, build_exe):
            self.assertEqual(PYVRP_NEXT_VERSION, module.PYVRP_NEXT_VERSION)

    def test_worker_and_parent_reject_alpha_rc_and_unverified_releases(self):
        for version in ("0.14.0a0", "0.14.0rc1", "0.14.1", "0.140.0", "0.13.4"):
            with self.subTest(version=version):
                with patch.object(worker.importlib.metadata, "version", return_value=version):
                    with self.assertRaisesRegex(worker.WorkerValidationError, "pinned PyVRP"):
                        worker._pyvrp_version()
                with self.assertRaisesRegex(runtime.PyVRPNextRuntimeError, "pinned PyVRP"):
                    runtime._validate_worker_metadata(worker._metadata(version), context="test")
                with patch.object(build_worker.importlib.metadata, "version", return_value=version):
                    with self.assertRaisesRegex(SystemExit, "requires stable"):
                        build_worker._require_experimental_environment()

    def test_self_check_detects_broken_adapter_not_just_native_import(self):
        with patch.object(worker, "_pyvrp_version", return_value=PYVRP_NEXT_VERSION), \
             patch.object(worker.importlib, "import_module", return_value=adapter), \
             patch.object(adapter, "PYVRP_AVAILABLE", False), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, worker.self_check())
        self.assertIn("adapter is not compatible", output.getvalue())

    def test_setup_defaults_to_pinned_binary_release_without_local_alpha_discovery(self):
        with patch.object(setup, "_run") as run, \
             patch.object(setup, "_verify_environment", return_value=PYVRP_NEXT_VERSION), \
             patch.object(Path, "is_file", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, setup.main(["--skip-project-requirements"]))
        installs = [call.args[0] for call in run.call_args_list
                    if "--only-binary=pyvrp" in call.args[0]]
        self.assertEqual(1, len(installs))
        self.assertEqual("pyvrp==0.14.0", installs[0][-1])

    def test_setup_rejects_alpha_wheel_before_mutating_environment(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(setup, "_run") as run:
            with self.assertRaisesRegex(SystemExit, "Only stable PyVRP"):
                setup.main(["--wheel", "pyvrp-0.14.0a0-cp314-cp314-win_amd64.whl"])
        run.assert_not_called()

    def test_request_rejects_conflicting_release(self):
        request = dict(protocol_version=1, kind="solve_request", solver_backend="pyvrp_next",
                       required_version_prefix="0.14", required_version="0.14.0a0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.pkl"
            path.write_bytes(pickle.dumps(request))
            with self.assertRaisesRegex(worker.WorkerValidationError, "pinned PyVRP release"):
                worker._read_request(path)

    def test_main_build_rejects_old_alpha_executable_metadata(self):
        import json
        from types import SimpleNamespace
        metadata = {**worker._metadata("0.14.0a0"), "ok": True, "kind": "self_check"}
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(build_exe.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(metadata))):
            with self.assertRaisesRegex(RuntimeError, "Invalid experimental worker self-check"):
                build_exe.verify_pyvrp_next_worker()


@unittest.skipUnless(adapter.PYVRP_AVAILABLE and adapter.PYVRP_VERSION == PYVRP_NEXT_VERSION,
                     "Requires isolated stable PyVRP 0.14.0 environment")
class NativeStable014Tests(unittest.TestCase):
    backend_name = "pyvrp_experimental"
    expected_version = "0.14.0"
    def test_dynamic_zone_discount_selects_target_vehicle_in_both_deferral_modes(self):
        from config import CenterZoneConfig, LocationConfig, VehicleType
        from tests.test_pyvrp_multitrip import DEPOT, make_config, make_customers, make_matrix, make_vehicle
        customers = make_customers(1, volume=1)
        customers[0].mandatory = True
        zone = CenterZoneConfig(name="Synthetic zone", center_coords=customers[0].coordinates,
                                radius_km=2, priority_vehicle_types=[VehicleType.CENTER_BUS.value],
                                discount_priority_vehicle=0.1, enable_restrictions=False,
                                restricted_vehicle_types=[], vehicle_penalties={})
        locations = LocationConfig(depot_location=DEPOT, center_zones=[zone])
        vehicles = [make_vehicle(config_id="regular"),
                    make_vehicle(config_id="preferred", vehicle_type=VehicleType.CENTER_BUS)]
        for strategy in ("legacy", "geographic"):
            with self.subTest(strategy=strategy):
                config = make_config(enable_multiple_trips=False, solver_type=self.backend_name,
                                     customer_dropping_strategy=strategy, allow_customer_skipping=True,
                                     pyvrp_use_extended_operators=True)
                solver = adapter.PyVRPSolver(config, vehicles, customers, make_matrix(customers),
                                             [DEPOT], location_config=locations)
                solution = solver.solve()
                self.assertTrue(solution.is_feasible)
                self.assertEqual([], solution.dropped_customers)
                self.assertEqual(1, len(solution.routes))
                self.assertEqual(VehicleType.CENTER_BUS, solution.routes[0].vehicle_type)

    def test_default_and_extended_operators_use_stable_names_and_keep_alternatives(self):
        from tests.test_pyvrp_multitrip import DEPOT, make_config, make_customers, make_matrix, make_vehicle
        from pyvrp.search import OPERATORS
        customers = make_customers(2)
        for extended in (False, True):
            with self.subTest(extended=extended):
                cfg = make_config(pyvrp_use_extended_operators=extended,
                                  pyvrp_use_library_penalty_defaults=False,
                                  pyvrp_penalty_target_feasible=0.6,
                                  pyvrp_num_neighbours=17, pyvrp_ils_history_length=35,
                                  pyvrp_min_perturbations=2, pyvrp_max_perturbations=12)
                solver = adapter.PyVRPSolver(cfg, [make_vehicle()], customers,
                                             make_matrix(customers), [DEPOT])
                params = solver._create_solve_params()
                names = {op.__name__ for op in params.operators}
                self.assertTrue(set(OPERATORS).issubset(params.operators))
                self.assertTrue({"RelocateAlternative", "ReplaceGroup", "ReplaceOptionalClient"} <= names)
                self.assertFalse(any(name.startswith("Exchange") for name in names))
                heavy = {"Relocate3", "Swap31", "Swap32", "Swap33"}
                self.assertEqual(heavy if extended else set(), names & heavy)
                self.assertEqual(len(names), len(params.operators))
                self.assertEqual(0.6, params.penalty.target_feasible)
                self.assertEqual(17, params.neighbourhood.num_neighbours)
                self.assertEqual(35, params.ils.history_length)
                self.assertEqual(2, params.perturbation.min_perturbations)
                self.assertEqual(12, params.perturbation.max_perturbations)

    def test_alternative_windows_preserve_customer_prize_in_cost_deltas(self):
        from pyvrp import Activity, ActivityType, Route, Solution
        from tests.test_customer_deferral import NativeRepairTests
        case = NativeRepairTests()
        deltas = []
        for grouped in (False, True):
            data, old, evaluator, _ = case.build_case(grouped=grouped)
            replacement = Solution(data, [Route(data, [Activity(ActivityType.CLIENT, 5)], 0)])
            self.assertTrue(replacement.is_feasible())
            deltas.append(evaluator.cost(old) - evaluator.cost(replacement))
        # PyVRP sums prizes of all alternative nodes. Unchosen alternatives
        # add a constant to every solution; the serve/drop incentive must stay
        # exactly one prize, not be multiplied by the number of windows.
        self.assertEqual([90000, 90000], deltas)

    def test_real_sidecar_roundtrip_preserves_windows_reload_and_client_service(self):
        from config import LocationConfig
        from warehouse_manager import WarehouseAllocation
        from tests.test_pyvrp_multitrip import DEPOT, make_config, make_customers, make_matrix, make_vehicle
        customers = make_customers(3, volume=1)
        for idx, customer in enumerate(customers):
            customer.mandatory = True
            customer.service_time_minutes = idx  # includes explicit zero, not van default
            customer.time_windows = [(60, 120), (480, 600)]  # only second is reachable
        vehicle = make_vehicle(capacity=2, service_time_minutes=7)
        allocation = WarehouseAllocation(customers, [], 2, 3, 0, 1.5)
        locations = LocationConfig(depot_location=DEPOT)
        for strategy in ("legacy", "geographic"):
            with self.subTest(strategy=strategy):
                config = make_config(solver_type=self.backend_name, customer_dropping_strategy=strategy,
                                     enable_customer_time_windows=True, allow_customer_skipping=True,
                                     pyvrp_use_extended_operators=True, pyvrp_next_worker_path="",
                                     pyvrp_next_worker_timeout_seconds=30, pyvrp_next_fallback_to_stable=False)
                solution = runtime.solve_cvrp_pyvrp_next(allocation, DEPOT, make_matrix(customers),
                                                       config, locations, [vehicle])
                self.assertEqual(self.expected_version, solution.solver_version)
                self.assertFalse(solution.solver_fallback_used)
                self.assertTrue(solution.is_feasible)
                self.assertEqual([], solution.dropped_customers)
                served = [c.id for route in solution.routes for c in route.customers]
                self.assertCountEqual([c.id for c in customers], served)
                self.assertEqual(2, len(solution.routes))
                self.assertEqual(1, solution.total_vehicles_used)
                for route in solution.routes:
                    for entry in route.schedule_entries:
                        self.assertGreaterEqual(entry["arrival_time_minutes"], 480)
                        self.assertLessEqual(entry["arrival_time_minutes"], 600)
                        expected = entry["customer"].service_time_minutes
                        self.assertEqual(expected, entry["service_time_minutes"])


if __name__ == "__main__":
    unittest.main()
