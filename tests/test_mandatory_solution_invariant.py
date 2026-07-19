import pickle
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
import pyvrp_next_runtime as runtime
import pyvrp_next_worker as worker


def customer(document, *, mandatory=True, customer_id="duplicate-id"):
    return SimpleNamespace(
        id=customer_id,
        name="Same business customer",
        coordinates=(42.7, 23.3),
        volume=1.0,
        original_gps_data="42.7,23.3",
        document=document,
        plas_doc=f"payment-{document}",
        source_id_skld="128",
        time_window_start_minutes=480,
        time_window_end_minutes=720,
        time_windows=[(480, 720)],
        delivery_comment="",
        grouped_documents=[{"document": document, "volume": 1.0}],
        service_time_minutes=5.0,
        mandatory=mandatory,
    )


def allocation(vehicle_customers, warehouse_customers=None):
    return SimpleNamespace(
        vehicle_customers=list(vehicle_customers),
        warehouse_customers=list(warehouse_customers or []),
    )


def solution(served_customers, dropped_customers=None):
    return SimpleNamespace(
        routes=[SimpleNamespace(customers=list(served_customers))],
        dropped_customers=list(dropped_customers or []),
        is_feasible=True,
        fitness_score=1.0,
    )


class MandatorySolutionInvariantTests(unittest.TestCase):
    def test_duplicate_business_ids_are_distinguished_after_pickle_round_trip(self):
        first = customer("doc-1")
        second = customer("doc-2")
        original_allocation = allocation([first, second])
        returned_solution = pickle.loads(
            pickle.dumps(solution([second, first]), protocol=pickle.HIGHEST_PROTOCOL)
        )

        summary = runtime.validate_mandatory_customer_solution(
            original_allocation,
            returned_solution,
            context="unit test",
        )

        self.assertEqual(2, summary["expected"])
        self.assertNotEqual(
            runtime.mandatory_visit_fingerprint(first),
            runtime.mandatory_visit_fingerprint(second),
        )

    def test_one_of_two_duplicate_business_ids_cannot_hide_as_served(self):
        first = customer("doc-1")
        second = customer("doc-2")

        with self.assertRaisesRegex(
            runtime.MandatoryVisitInvariantError,
            r"missing 1 occurrence.*doc-2",
        ):
            runtime.validate_mandatory_customer_solution(
                allocation([first, second]),
                solution([first]),
                context="unit test",
            )

    def test_mandatory_visit_cannot_be_served_twice_or_also_dropped(self):
        required = customer("doc-1")
        with self.assertRaisesRegex(
            runtime.MandatoryVisitInvariantError,
            r"extra occurrence|same visit object",
        ):
            runtime.validate_mandatory_customer_solution(
                allocation([required]),
                solution([required, required]),
                context="unit test",
            )

        with self.assertRaisesRegex(
            runtime.MandatoryVisitInvariantError,
            "also reported dropped",
        ):
            runtime.validate_mandatory_customer_solution(
                allocation([required]),
                solution([required], [required]),
                context="unit test",
            )

    def test_mandatory_visit_in_warehouse_is_always_invalid(self):
        required = customer("warehouse-doc")
        with self.assertRaisesRegex(
            runtime.MandatoryVisitInvariantError,
            "remain in warehouse",
        ):
            runtime.validate_mandatory_customer_solution(
                allocation([], [required]),
                solution([]),
                context="unit test",
            )

    def test_common_output_boundary_stops_before_output_handler_is_created(self):
        required = customer("missing-doc")
        with (
            patch.object(main, "OutputHandler") as output_handler,
            self.assertRaisesRegex(main.MandatoryCustomerError, "Решението е отхвърлено"),
        ):
            main.process_results(
                solution([]),
                SimpleNamespace(depot_location=(42.0, 23.0)),
                allocation([required]),
                1.0,
                [],
            )

        output_handler.assert_not_called()


class MandatoryWorkerProtocolTests(unittest.TestCase):
    def test_worker_metadata_advertises_hard_mandatory_capability(self):
        metadata = worker._metadata("0.14.0")
        self.assertIs(
            True,
            metadata["capabilities"][runtime.HARD_MANDATORY_CUSTOMERS_CAPABILITY],
        )
        runtime._validate_worker_metadata(metadata, context="unit test")

    def test_worker_rejects_solution_that_omits_mandatory_visit(self):
        required = customer("worker-doc")
        request = {
            "protocol_version": worker.PROTOCOL_VERSION,
            "kind": "solve_request",
            "solver_backend": worker.WORKER_PROTOCOL_BACKEND,
            "required_version_prefix": worker.REQUIRED_PYVRP_VERSION_PREFIX,
            "required_capabilities": sorted(runtime.REQUIRED_WORKER_CAPABILITIES),
            "payload": {
                "allocation": allocation([required]),
                "depot_location": (42.0, 23.0),
                "distance_matrix": SimpleNamespace(),
                "config": SimpleNamespace(),
                "location_config": SimpleNamespace(),
                "vehicle_configs": [],
            },
        }
        fake_solver = types.ModuleType("pyvrp_solver")
        fake_solver.solve_cvrp_pyvrp = lambda **_kwargs: solution([])

        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "request.pkl"
            result_path = Path(temp_dir) / "result.pkl"
            request_path.write_bytes(pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL))
            with (
                patch.object(worker, "_pyvrp_version", return_value="0.14.0"),
                patch.object(worker, "_configure_worker_logging"),
                patch.dict(sys.modules, {"pyvrp_solver": fake_solver}),
            ):
                exit_code = worker.run_worker(str(request_path), str(result_path))

            envelope = pickle.loads(result_path.read_bytes())

        self.assertEqual(1, exit_code)
        self.assertFalse(envelope["ok"])
        self.assertEqual("MandatoryVisitInvariantError", envelope["error_type"])
        self.assertIn("missing 1", envelope["error"])
        self.assertTrue(
            envelope["capabilities"][runtime.HARD_MANDATORY_CUSTOMERS_CAPABILITY]
        )


if __name__ == "__main__":
    unittest.main()
