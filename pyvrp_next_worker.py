"""Worker entry point for the process-isolated PyVRP 0.14 runtime.

This process must be started by a Python environment (or PyInstaller sidecar)
that contains the pinned stable PyVRP 0.14.0. It intentionally does not read ``config.py`` to
choose a solver: every solve input is provided by the parent in the trusted
local request envelope.
"""

from __future__ import annotations

import argparse
import io
import importlib
import importlib.metadata
import json
import logging
import os
import pickle
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict

from pyvrp_next_runtime import (
    HARD_MANDATORY_CUSTOMERS_CAPABILITY,
    validate_mandatory_customer_solution,
)
from customer_deferral import GEOGRAPHIC_CAPABILITY
from service_time import SERVICE_FORMULA_CAPABILITY
from pyvrp_next_version import PYVRP_NEXT_VERSION
from pyvrp_backends import STABLE
from tsp_pyvrp import TSP_CAPABILITY


PROTOCOL_VERSION = 1
WORKER_PROTOCOL_BACKEND = "pyvrp_next"
REQUIRED_PYVRP_VERSION_PREFIX = "0.14"
WORKER_CAPABILITIES = {
    "tsp_persistent_v1": True,
    TSP_CAPABILITY: True,
    'physical_bus_schedules_v1': True,
    SERVICE_FORMULA_CAPABILITY: True,
    HARD_MANDATORY_CUSTOMERS_CAPABILITY: True,
    GEOGRAPHIC_CAPABILITY: True,
}


class WorkerValidationError(RuntimeError):
    """Raised when this process is not a valid PyVRP 0.14 worker."""


def _configure_stdio() -> None:
    """Force UTF-8 pipes even when the frozen Windows worker starts as cp1252."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue

        try:
            stream.reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=True,
            )
            continue
        except (AttributeError, OSError, ValueError):
            pass

        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        try:
            setattr(
                sys,
                stream_name,
                io.TextIOWrapper(
                    buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                    write_through=True,
                ),
            )
        except (OSError, ValueError):
            pass


def _pyvrp_version(backend=STABLE) -> str:
    try:
        version = importlib.metadata.version("pyvrp")
    except importlib.metadata.PackageNotFoundError as exc:
        raise WorkerValidationError("PyVRP is not installed in the worker environment") from exc

    try:
        importlib.import_module("pyvrp")
    except Exception as exc:
        raise WorkerValidationError(f"PyVRP cannot be imported: {exc}") from exc

    if version != backend.version:
        raise WorkerValidationError(
            f"This worker requires pinned PyVRP {backend.version}, but found {version}. "
            "Run setup_pyvrp_next.py and rebuild the worker."
        )
    if backend.source_commit:
        provenance = importlib.metadata.distribution("pyvrp").read_text("direct_url.json")
        commit = json.loads(provenance or "{}").get("vcs_info", {}).get("commit_id")
        if commit != backend.source_commit:
            raise WorkerValidationError("PyVRP 1.x source provenance does not match the pinned commit")
    return version


def _metadata(version: str, backend=STABLE) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "solver_backend": backend.protocol,
        "source_commit": backend.source_commit,
        "solver_version": version,
        "capabilities": dict(WORKER_CAPABILITIES),
    }


def self_check(backend=STABLE) -> int:
    try:
        version = _pyvrp_version(backend)
        # Advertise the capability only after its module/native imports work.
        importlib.import_module("pyvrp_deferral_search")
        importlib.import_module("tsp_pyvrp")
        adapter = importlib.import_module("pyvrp_solver")
        if not adapter.PYVRP_AVAILABLE or adapter.PYVRP_VERSION != version:
            raise WorkerValidationError("PyVRP solver adapter is not compatible with this runtime")
        payload = {
            **_metadata(version, backend),
            "kind": "self_check",
            "ok": True,
            "python_version": sys.version.split()[0],
            "executable": sys.executable,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        payload = {
            **_metadata("", backend),
            "kind": "self_check",
            "ok": False,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 2


def _atomic_pickle_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as file_handle:
            pickle.dump(value, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_request(path: Path, backend=STABLE) -> Dict[str, Any]:
    try:
        with path.open("rb") as file_handle:
            request = pickle.load(file_handle)
    except Exception as exc:
        raise WorkerValidationError(f"Could not read worker request: {exc}") from exc

    if not isinstance(request, dict):
        raise WorkerValidationError("Worker request is not an envelope")
    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise WorkerValidationError(
            "Worker request protocol mismatch: "
            f"expected {PROTOCOL_VERSION}, got {request.get('protocol_version')!r}"
        )
    if request.get("kind") != "solve_request":
        raise WorkerValidationError(
            f"Unexpected worker request kind: {request.get('kind')!r}"
        )
    if request.get("solver_backend") != backend.protocol:
        raise WorkerValidationError(
            f"Unexpected worker request backend: {request.get('solver_backend')!r}"
        )
    if request.get("required_version_prefix") != backend.prefix:
        raise WorkerValidationError(
            "Parent and worker disagree about the required PyVRP version prefix"
        )
    if request.get("required_version", backend.version) != backend.version:
        raise WorkerValidationError("Parent and worker disagree about the pinned PyVRP release")
    if backend.source_commit and request.get("source_commit") != backend.source_commit:
        raise WorkerValidationError("Parent and worker disagree about the PyVRP source commit")

    required_capabilities = request.get("required_capabilities")
    if not isinstance(required_capabilities, (list, tuple, set)):
        raise WorkerValidationError(
            "Worker request has no valid required_capabilities declaration"
        )
    required_capabilities = {
        str(capability).strip()
        for capability in required_capabilities
        if str(capability).strip()
    }
    missing_capabilities = sorted(
        capability
        for capability in required_capabilities
        if WORKER_CAPABILITIES.get(capability) is not True
    )
    if missing_capabilities:
        raise WorkerValidationError(
            "Worker does not provide required capability/capabilities: "
            + ", ".join(missing_capabilities)
        )
    if HARD_MANDATORY_CUSTOMERS_CAPABILITY not in required_capabilities:
        raise WorkerValidationError(
            "Parent request must require the hard_mandatory_customers capability"
        )

    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise WorkerValidationError("Worker request has no payload object")
    required = {
        "allocation",
        "depot_location",
        "distance_matrix",
        "config",
        "location_config",
        "vehicle_configs",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise WorkerValidationError(
            "Worker request is missing fields: " + ", ".join(missing)
        )
    return payload


def _configure_worker_logging() -> None:
    """Send solver and PyVRP progress records to the parent process pipe."""
    _configure_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )
    # PyVRP installs its own package handler.  Keep one parent-owned handler so
    # progress records are not written twice when they also propagate to root.
    pyvrp_logger = logging.getLogger("pyvrp")
    pyvrp_logger.handlers.clear()
    pyvrp_logger.propagate = True
    pyvrp_logger.setLevel(logging.INFO)


def run_worker(input_path: str, output_path: str, backend=STABLE) -> int:
    output = Path(output_path).resolve()
    version = ""
    try:
        version = _pyvrp_version(backend)
        payload = _read_request(Path(input_path).resolve(), backend)
        if backend.source_commit and getattr(payload["config"], "solver_type", "") != backend.public:
            raise WorkerValidationError("Payload solver does not match the selected worker")

        # Import only after the runtime and request have passed strict checks.
        # The adapter may provide conditional compatibility for PyVRP 0.14,
        # but solver selection itself is never read from global config here.
        from pyvrp_solver import solve_cvrp_pyvrp
        _configure_worker_logging()

        solution = solve_cvrp_pyvrp(
            allocation=payload["allocation"],
            depot_location=payload["depot_location"],
            distance_matrix=payload["distance_matrix"],
            config=payload["config"],
            location_config=payload["location_config"],
            vehicle_configs=payload["vehicle_configs"],
        )
        mandatory_validation = validate_mandatory_customer_solution(
            payload["allocation"],
            solution,
            context="experimental PyVRP worker result validation",
        )
        envelope = {
            **_metadata(version, backend),
            "kind": "solve_result",
            "ok": True,
            "solution": solution,
            "mandatory_validation": mandatory_validation,
        }
        _atomic_pickle_dump(output, envelope)
        return 0
    except Exception as exc:
        envelope = {
            **_metadata(version, backend),
            "kind": "solve_result",
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        try:
            _atomic_pickle_dump(output, envelope)
        except Exception as output_exc:
            print(
                f"Could not write experimental worker error result: {output_exc}",
                file=sys.stderr,
            )
        print(f"Experimental PyVRP worker failed: {exc}", file=sys.stderr)
        return 1


def run_tsp_worker(input_path: str, output_path: str, backend=STABLE) -> int:
    request_id = ""
    version = ""
    try:
        version = _pyvrp_version(backend)
        request = json.loads(Path(input_path).read_text(encoding="utf-8"))
        request_id = request.get("request_id", "")
        if request.get("kind") != TSP_CAPABILITY or not request_id:
            raise WorkerValidationError("Invalid TSP request envelope")
        from tsp_pyvrp import solve_native
        result = dict(ok=True, **solve_native(request["payload"]))
    except Exception as exc:
        result = dict(ok=False, error=str(exc))
        traceback.print_exc()
    result.update(kind=TSP_CAPABILITY, request_id=request_id, solver_version=version)
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return 0 if result["ok"] else 1


def serve_tsp(backend=STABLE) -> int:
    """Load native libraries once; all transport stays on inherited private pipes."""
    import contextlib
    import queue
    import threading
    from tsp_pyvrp_runtime import PROTOCOL
    from tsp_pyvrp import solve_native
    version = _pyvrp_version(backend)
    protocol_output = sys.stdout
    sys.stdin.reconfigure(encoding="utf-8")
    # Keep progress, warnings and accidental prints away from the JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        import pyvrp
        from pyvrp.IteratedLocalSearch import IteratedLocalSearchParams
        from pyvrp.stop import MaxRuntime
    def send(message):
        message.update(protocol=PROTOCOL, solver_version=version)
        protocol_output.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
        protocol_output.flush()
    requests = queue.Queue()
    def read_requests():
        for line in sys.stdin:
            requests.put(line)
        # Parent closed its pipe or died. Also stops a currently hung native solve.
        os._exit(0)
    threading.Thread(target=read_requests, daemon=True).start()
    send(dict(kind="ready", pid=os.getpid()))
    while True:
        request_id = ""
        try:
            request = json.loads(requests.get())
            request_id = request.get("request_id", "")
            if request.get("protocol") != PROTOCOL or not request_id:
                raise WorkerValidationError("Invalid persistent TSP request")
            with contextlib.redirect_stdout(sys.stderr):
                result = dict(ok=True, **solve_native(request["payload"]))
        except Exception as exc:
            result = dict(ok=False, error=str(exc))
            traceback.print_exc(file=sys.stderr)
        result.update(kind=TSP_CAPABILITY, request_id=request_id)
        send(result)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental PyVRP 0.14 worker")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print worker protocol and installed PyVRP version as JSON.",
    )
    parser.add_argument("input_pickle", nargs="?")
    parser.add_argument("output_pickle", nargs="?")
    parser.add_argument("--tsp", action="store_true", help="Solve a single-driver TSP JSON request.")
    parser.add_argument("--tsp-serve", action="store_true", help="Persistent private JSON-pipe TSP worker.")
    return parser


def main(argv: list[str] | None = None, *, backend=STABLE) -> int:
    _configure_stdio()
    args = _build_parser().parse_args(argv)
    if args.self_check:
        return self_check(backend)
    if args.tsp_serve:
        return serve_tsp(backend)
    if not args.input_pickle or not args.output_pickle:
        print("input_pickle and output_pickle are required", file=sys.stderr)
        return 2
    if args.tsp:
        return run_tsp_worker(args.input_pickle, args.output_pickle, backend)
    return run_worker(args.input_pickle, args.output_pickle, backend)


if __name__ == "__main__":
    raise SystemExit(main())
