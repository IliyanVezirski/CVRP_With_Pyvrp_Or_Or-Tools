import os
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pyvrp_next_runtime as runtime


FAKE_WORKER = r'''import json
import os
import pickle
import sys
import time
from types import SimpleNamespace

mode = os.environ.get("FAKE_PYVRP_NEXT_MODE", "success")
version = "0.13.4" if mode == "self_check_version_mismatch" else "0.14.0a0"
metadata = {
    "protocol_version": 1,
    "solver_backend": "pyvrp_next",
    "solver_version": version,
    "capabilities": (
        {} if mode == "missing_capability"
        else {"hard_mandatory_customers": True}
    ),
}

if "--self-check" in sys.argv:
    print(json.dumps({**metadata, "kind": "self_check", "ok": True}))
    raise SystemExit(0)

input_path, output_path = sys.argv[1], sys.argv[2]
if mode == "timeout":
    time.sleep(5)
if mode == "progress":
    print("Search progress: iteration 1", flush=True)
    print("Search progress: iteration 1", flush=True)
    print("Search progress: iteration 1", flush=True)
    time.sleep(0.05)
    print("Search progress: iteration 2", flush=True)

with open(input_path, "rb") as fh:
    request = pickle.load(fh)

result_version = "0.14.0a1" if mode == "result_version_mismatch" else version
solution = SimpleNamespace(
    marker="fake-next-solution",
    received_depot=request["payload"]["depot_location"],
    routes=(
        [SimpleNamespace(customers=list(request["payload"]["allocation"].vehicle_customers))]
        if mode == "mandatory_served"
        else []
    ),
    dropped_customers=[],
)
envelope = {
    "protocol_version": 1,
    "solver_backend": "pyvrp_next",
    "solver_version": result_version,
    "capabilities": metadata["capabilities"],
    "kind": "solve_result",
    "ok": mode != "worker_error",
    "solution": solution,
}
if mode == "worker_error":
    envelope["error"] = "synthetic worker failure"

with open(output_path, "wb") as fh:
    pickle.dump(envelope, fh, protocol=pickle.HIGHEST_PROTOCOL)
raise SystemExit(1 if mode == "worker_error" else 0)
'''


def make_config(worker_path: str, **overrides):
    values = {
        "pyvrp_next_worker_path": worker_path,
        "pyvrp_next_worker_timeout_seconds": 5,
        "pyvrp_next_fallback_to_stable": False,
        "time_limit_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def solve(config, allocation=None):
    return runtime.solve_cvrp_pyvrp_next(
        allocation=allocation or SimpleNamespace(customers=["one"]),
        depot_location=(42.0, 23.0),
        distance_matrix=SimpleNamespace(distances=[[0]], durations=[[0]]),
        config=config,
        location_config=SimpleNamespace(depot_location=(42.0, 23.0)),
        vehicle_configs=[SimpleNamespace(config_id="van-1")],
    )


class PyVRPNextRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.worker = self.root / "fake_pyvrp_next_worker.py"
        self.worker.write_text(FAKE_WORKER, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_explicit_worker_path_has_priority(self):
        config = make_config(str(self.worker))
        with patch.object(runtime, "_source_worker_command") as source_command:
            command = runtime._resolve_worker_command(config)

        self.assertEqual([sys.executable, str(self.worker.resolve())], command)
        source_command.assert_not_called()

    def test_source_environment_is_resolved_without_using_stable_venv(self):
        project = self.root / "project"
        python_exe = project / ".venv-pyvrp-next" / "Scripts" / "python.exe"
        worker_py = project / "pyvrp_next_worker.py"
        python_exe.parent.mkdir(parents=True)
        python_exe.write_bytes(b"")
        worker_py.write_text("", encoding="utf-8")

        with patch.object(runtime, "PROJECT_DIR", project):
            command = runtime._resolve_worker_command(SimpleNamespace(pyvrp_next_worker_path=""))

        self.assertEqual([str(python_exe), str(worker_py)], command)
        self.assertNotIn(str(project / ".venv"), command)

    def test_missing_explicit_worker_fails_clearly(self):
        missing = self.root / "missing-worker.exe"
        config = make_config(str(missing))

        with self.assertRaisesRegex(runtime.PyVRPNextRuntimeError, "does not exist"):
            solve(config)

    def test_successful_fake_worker_round_trip_and_metadata(self):
        config = make_config(str(self.worker))

        with patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "success"}):
            result = solve(config)

        self.assertEqual("fake-next-solution", result.marker)
        self.assertEqual((42.0, 23.0), result.received_depot)
        self.assertEqual("pyvrp_experimental", result.solver_backend)
        self.assertEqual("pyvrp_experimental", result.solver_actual_backend)
        self.assertEqual("0.14.0a0", result.solver_version)
        self.assertFalse(result.solver_fallback_used)

    def test_worker_progress_is_forwarded_to_parent_logger(self):
        config = make_config(str(self.worker))

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "progress"}),
            self.assertLogs("pyvrp_next_runtime", level="INFO") as captured,
        ):
            result = solve(config)

        self.assertEqual("fake-next-solution", result.marker)
        output = "\n".join(captured.output)
        self.assertIn("[PyVRP 0.14] Search progress: iteration 1", output)
        self.assertIn("[PyVRP 0.14] Search progress: iteration 2", output)
        self.assertEqual(1, output.count("Search progress: iteration 1"))
        self.assertIn("Previous progress line repeated 2 more times", output)

    def test_real_worker_logging_emits_progress_logger_to_stdout(self):
        script = (
            "import logging, sys; "
            "import pyvrp_next_worker as worker; "
            "package_logger = logging.getLogger('pyvrp'); "
            "package_logger.addHandler(logging.StreamHandler(sys.stdout)); "
            "package_logger.setLevel(logging.INFO); "
            "worker._configure_worker_logging(); "
            "logging.getLogger('pyvrp.ProgressPrinter').info('progress-marker')"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=runtime.PROJECT_DIR,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("progress-marker", completed.stdout)
        self.assertEqual(1, completed.stdout.count("progress-marker"))

    def test_worker_reconfigures_cp1252_stdout_before_logging_cyrillic(self):
        script = r'''
import io
import logging
import sys
import pyvrp_next_worker as worker

raw = io.BytesIO()
sys.stdout = io.TextIOWrapper(raw, encoding="cp1252")
worker._configure_worker_logging()
logging.getLogger("worker-encoding-test").info("Ред на депата")
sys.stdout.flush()
assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
assert raw.getvalue().decode("utf-8").strip() == "Ред на депата"
'''
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=runtime.PROJECT_DIR,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_solve_result_rejects_non_014_worker(self):
        config = make_config(str(self.worker))

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "self_check_version_mismatch"}),
            self.assertRaisesRegex(runtime.PyVRPNextRuntimeError, r"must use PyVRP 0\.14"),
        ):
            solve(config)

    def test_solve_result_rejects_worker_without_hard_mandatory_capability(self):
        config = make_config(str(self.worker))

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "missing_capability"}),
            self.assertRaisesRegex(
                runtime.PyVRPNextRuntimeError,
                "hard_mandatory_customers",
            ),
        ):
            solve(config)

    def test_self_check_rejects_worker_without_hard_mandatory_capability(self):
        metadata = {
            "protocol_version": runtime.PROTOCOL_VERSION,
            "solver_backend": "pyvrp_next",
            "solver_version": "0.14.0",
            "kind": "self_check",
            "ok": True,
            "capabilities": {},
        }
        completed = subprocess.CompletedProcess(
            args=["fake-worker", "--self-check"],
            returncode=0,
            stdout=json.dumps(metadata),
            stderr="",
        )

        with (
            patch.object(runtime, "_run_process", return_value=completed),
            self.assertRaisesRegex(runtime.PyVRPNextRuntimeError, "hard_mandatory_customers"),
        ):
            runtime._self_check(["fake-worker"], 5)

    def test_parent_revalidates_mandatory_visits_returned_by_worker(self):
        config = make_config(str(self.worker))
        mandatory = SimpleNamespace(id="same-id", document="doc-1", mandatory=True)
        allocation = SimpleNamespace(
            vehicle_customers=[mandatory],
            warehouse_customers=[],
        )

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "success"}),
            self.assertRaisesRegex(runtime.MandatoryVisitInvariantError, "missing 1"),
        ):
            solve(config, allocation)

        with patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "mandatory_served"}):
            result = solve(config, allocation)

        self.assertEqual(["same-id"], [customer.id for customer in result.routes[0].customers])

    def test_solve_does_not_start_a_redundant_self_check_process(self):
        config = make_config(str(self.worker))

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "success"}),
            patch.object(runtime, "_self_check", side_effect=AssertionError("unexpected self-check")),
        ):
            result = solve(config)

        self.assertEqual("fake-next-solution", result.marker)

    def test_timeout_does_not_silently_fallback(self):
        config = make_config(
            str(self.worker),
            pyvrp_next_worker_timeout_seconds=0.2,
        )

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "timeout"}),
            self.assertRaisesRegex(runtime.PyVRPNextRuntimeError, "timed out"),
        ):
            solve(config)

    def test_timeout_can_use_explicit_stable_fallback(self):
        config = make_config(
            str(self.worker),
            pyvrp_next_worker_timeout_seconds=0.2,
            pyvrp_next_fallback_to_stable=True,
        )
        fake_pyvrp_solver = types.ModuleType("pyvrp_solver")
        fake_pyvrp_solver.solve_cvrp_pyvrp = lambda **_kwargs: SimpleNamespace(marker="stable")

        with (
            patch.dict(os.environ, {"FAKE_PYVRP_NEXT_MODE": "timeout"}),
            patch.dict(sys.modules, {"pyvrp_solver": fake_pyvrp_solver}),
        ):
            result = solve(config)

        self.assertEqual("stable", result.marker)
        self.assertEqual("pyvrp_experimental", result.solver_requested)
        self.assertEqual("pyvrp", result.solver_backend)
        self.assertEqual("pyvrp", result.solver_used)
        self.assertEqual("pyvrp", result.solver_actual_backend)
        self.assertTrue(result.solver_fallback_used)
        self.assertIn("timed out", result.solver_fallback_reason)

    def test_automatic_timeout_is_solver_limit_plus_sixty_seconds(self):
        config = make_config(
            str(self.worker),
            pyvrp_next_worker_timeout_seconds=0,
            time_limit_seconds=37,
        )
        self.assertEqual(97, runtime._worker_timeout_seconds(config))


if __name__ == "__main__":
    unittest.main()
