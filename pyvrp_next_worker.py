"""Worker entry point for the process-isolated PyVRP 0.14 runtime.

This process must be started by a Python environment (or PyInstaller sidecar)
that contains PyVRP 0.14.*.  It intentionally does not read ``config.py`` to
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


PROTOCOL_VERSION = 1
WORKER_PROTOCOL_BACKEND = "pyvrp_next"
REQUIRED_PYVRP_VERSION_PREFIX = "0.14"
WORKER_CAPABILITIES = {
    HARD_MANDATORY_CUSTOMERS_CAPABILITY: True,
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


def _pyvrp_version() -> str:
    try:
        version = importlib.metadata.version("pyvrp")
    except importlib.metadata.PackageNotFoundError as exc:
        raise WorkerValidationError("PyVRP is not installed in the worker environment") from exc

    try:
        importlib.import_module("pyvrp")
    except Exception as exc:
        raise WorkerValidationError(f"PyVRP cannot be imported: {exc}") from exc

    if not version.startswith(REQUIRED_PYVRP_VERSION_PREFIX):
        raise WorkerValidationError(
            f"This worker requires PyVRP 0.14.*, but found {version}"
        )
    return version


def _metadata(version: str) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "solver_backend": WORKER_PROTOCOL_BACKEND,
        "solver_version": version,
        "capabilities": dict(WORKER_CAPABILITIES),
    }


def self_check() -> int:
    try:
        version = _pyvrp_version()
        payload = {
            **_metadata(version),
            "kind": "self_check",
            "ok": True,
            "python_version": sys.version.split()[0],
            "executable": sys.executable,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        payload = {
            **_metadata(""),
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


def _read_request(path: Path) -> Dict[str, Any]:
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
    if request.get("solver_backend") != WORKER_PROTOCOL_BACKEND:
        raise WorkerValidationError(
            f"Unexpected worker request backend: {request.get('solver_backend')!r}"
        )
    if request.get("required_version_prefix") != REQUIRED_PYVRP_VERSION_PREFIX:
        raise WorkerValidationError(
            "Parent and worker disagree about the required PyVRP version prefix"
        )

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


def run_worker(input_path: str, output_path: str) -> int:
    output = Path(output_path).resolve()
    version = ""
    try:
        version = _pyvrp_version()
        payload = _read_request(Path(input_path).resolve())

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
            **_metadata(version),
            "kind": "solve_result",
            "ok": True,
            "solution": solution,
            "mandatory_validation": mandatory_validation,
        }
        _atomic_pickle_dump(output, envelope)
        return 0
    except Exception as exc:
        envelope = {
            **_metadata(version),
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental PyVRP 0.14 worker")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print worker protocol and installed PyVRP version as JSON.",
    )
    parser.add_argument("input_pickle", nargs="?")
    parser.add_argument("output_pickle", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = _build_parser().parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.input_pickle or not args.output_pickle:
        print("input_pickle and output_pickle are required", file=sys.stderr)
        return 2
    return run_worker(args.input_pickle, args.output_pickle)


if __name__ == "__main__":
    raise SystemExit(main())
