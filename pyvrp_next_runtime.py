"""Process-isolated bridge to the experimental PyVRP 0.14 runtime.

The production application deliberately keeps PyVRP 0.13 in its own Python
environment.  This module starts a separately built worker that owns PyVRP
0.14, exchanges one trusted local request through a random temporary
directory, and returns the normal :class:`CVRPSolution` object.

The pickle files are an internal IPC format.  They are never accepted from an
API request or a caller-provided path.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import unicodedata
import uuid
from collections import Counter, deque
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


PROTOCOL_VERSION = 1
WORKER_PROTOCOL_BACKEND = "pyvrp_next"
PUBLIC_SOLVER_BACKEND = "pyvrp_experimental"
REQUIRED_PYVRP_VERSION_PREFIX = "0.14"
HARD_MANDATORY_CUSTOMERS_CAPABILITY = "hard_mandatory_customers"
REQUIRED_WORKER_CAPABILITIES = frozenset({HARD_MANDATORY_CUSTOMERS_CAPABILITY})

# The worker timeout covers the complete sidecar lifetime, not only PyVRP's
# MaxRuntime search.  Frozen workers must first unpack/import, construct the
# model and all profile-specific edges, and must afterwards validate and
# serialise the solution.  Large instances (300+ clients, several profiles and
# parallel workers) have been observed to need more than the old 60 second
# allowance even though the solver itself stopped exactly on time.
DEFAULT_WORKER_OVERHEAD_SECONDS = 180.0
PROJECT_DIR = Path(__file__).resolve().parent
WORKER_FILENAME = "CVRP_PyVRP_Next_Worker.exe"
logger = logging.getLogger(__name__)


class PyVRPNextRuntimeError(RuntimeError):
    """Raised when the experimental worker cannot safely complete a solve."""


class MandatoryVisitInvariantError(RuntimeError):
    """Raised when a mandatory allocation visit is not served exactly once."""


_VISIT_FINGERPRINT_FIELDS = (
    "id",
    "name",
    "coordinates",
    "volume",
    "original_gps_data",
    "document",
    "plas_doc",
    "source_id_skld",
    "time_window_start_minutes",
    "time_window_end_minutes",
    "time_windows",
    "delivery_comment",
    "grouped_documents",
    "service_time_minutes",
    "mandatory",
)


def _normalise_fingerprint_value(value: Any) -> Any:
    """Return a JSON-stable representation without process-local identities."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return _normalise_fingerprint_value(value.value)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "__nan__"
        if math.isinf(value):
            return "__positive_infinity__" if value > 0 else "__negative_infinity__"
        if value == 0:
            return 0.0
        return value
    if isinstance(value, Mapping):
        return {
            unicodedata.normalize("NFC", str(key)): _normalise_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise_fingerprint_value(item) for item in value]
    return unicodedata.normalize("NFC", str(value))


def mandatory_visit_fingerprint(customer: Any) -> str:
    """Build a deterministic visit identity that survives process pickling.

    External customer IDs are deliberately only one component: separate visits
    may share that ID while having different documents, coordinates, volumes,
    or time windows. Exact duplicate records are represented by occurrence
    counts in :func:`validate_mandatory_customer_solution`.
    """
    payload = {
        field_name: _normalise_fingerprint_value(getattr(customer, field_name, None))
        for field_name in _VISIT_FINGERPRINT_FIELDS
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_mandatory_visit(customer: Any) -> bool:
    return bool(getattr(customer, "mandatory", False))


def _customer_label(customer: Any) -> str:
    customer_id = str(getattr(customer, "id", "") or "").strip()
    document = str(getattr(customer, "document", "") or "").strip()
    name = str(getattr(customer, "name", "") or "").strip()
    label = customer_id or name or "<without id>"
    if document:
        label += f"/document {document}"
    return label


def _mandatory_counter(customers: Iterable[Any]) -> Counter[str]:
    return Counter(
        mandatory_visit_fingerprint(customer)
        for customer in customers
        if _is_mandatory_visit(customer)
    )


def validate_mandatory_customer_solution(
    allocation: Any,
    solution: Any,
    *,
    context: str = "solution acceptance",
) -> Dict[str, int]:
    """Enforce the application-wide hard mandatory-customer invariant.

    Every mandatory visit assigned to the solver must occur on routes exactly
    once. It must not also be reported as dropped, and no mandatory visit may
    remain in the warehouse allocation. The comparison is content-based and
    count-aware, so it remains valid after pickle round-trips and does not
    collapse separate visits that merely share a business customer ID.
    """
    vehicle_customers = list(getattr(allocation, "vehicle_customers", None) or [])
    warehouse_customers = list(getattr(allocation, "warehouse_customers", None) or [])
    expected_customers = [customer for customer in vehicle_customers if _is_mandatory_visit(customer)]
    warehouse_mandatory = [customer for customer in warehouse_customers if _is_mandatory_visit(customer)]

    expected = _mandatory_counter(expected_customers)
    routes = list(getattr(solution, "routes", None) or []) if solution is not None else []
    served_customers = [
        customer
        for route in routes
        for customer in (getattr(route, "customers", None) or [])
    ]
    dropped_customers = list(getattr(solution, "dropped_customers", None) or []) if solution is not None else []
    served = Counter(mandatory_visit_fingerprint(customer) for customer in served_customers)
    served_mandatory = _mandatory_counter(served_customers)
    dropped = Counter(mandatory_visit_fingerprint(customer) for customer in dropped_customers)
    dropped_mandatory = _mandatory_counter(dropped_customers)

    labels: Dict[str, str] = {}
    for customer in [*expected_customers, *warehouse_mandatory, *served_customers, *dropped_customers]:
        labels.setdefault(mandatory_visit_fingerprint(customer), _customer_label(customer))

    problems: list[str] = []
    if warehouse_mandatory:
        preview = ", ".join(_customer_label(customer) for customer in warehouse_mandatory[:5])
        problems.append(
            f"{len(warehouse_mandatory)} mandatory visit(s) remain in warehouse [{preview}]"
        )

    for fingerprint, required_count in sorted(expected.items()):
        served_count = served.get(fingerprint, 0)
        if served_count < required_count:
            problems.append(
                f"missing {required_count - served_count} occurrence(s) of {labels[fingerprint]} "
                f"[{fingerprint[:12]}]"
            )
        elif served_count > required_count:
            problems.append(
                f"served {served_count - required_count} extra occurrence(s) of {labels[fingerprint]} "
                f"[{fingerprint[:12]}]"
            )

        dropped_count = dropped.get(fingerprint, 0)
        if dropped_count:
            problems.append(
                f"{labels[fingerprint]} is also reported dropped {dropped_count} time(s) "
                f"[{fingerprint[:12]}]"
            )

    for fingerprint, served_count in sorted((served_mandatory - expected).items()):
        problems.append(
            f"unexpected mandatory visit served {served_count} time(s): "
            f"{labels.get(fingerprint, fingerprint[:12])} [{fingerprint[:12]}]"
        )
    for fingerprint, dropped_count in sorted((dropped_mandatory - expected).items()):
        problems.append(
            f"unexpected mandatory visit reported dropped {dropped_count} time(s): "
            f"{labels.get(fingerprint, fingerprint[:12])} [{fingerprint[:12]}]"
        )

    # A solver must not reuse the exact same visit object on more than one
    # route/course. Pickle preserves aliasing within the returned solution, so
    # this catches an otherwise ambiguous duplicate even for identical rows.
    served_object_counts = Counter(
        id(customer)
        for customer in served_customers
        if _is_mandatory_visit(customer)
    )
    duplicated_objects = sum(count - 1 for count in served_object_counts.values() if count > 1)
    if duplicated_objects:
        problems.append(f"the same visit object is reused {duplicated_objects} extra time(s) on routes")

    if problems:
        raise MandatoryVisitInvariantError(
            f"Mandatory-customer invariant failed during {context}: " + "; ".join(problems)
        )

    return {
        "expected": sum(expected.values()),
        "served": sum(expected.values()),
        "dropped": 0,
        "warehouse": 0,
    }


def _normalise_path(raw_path: Any) -> Path | None:
    text = os.path.expandvars(os.path.expanduser(str(raw_path or "").strip()))
    if not text:
        return None
    return Path(text).resolve()


def _command_for_explicit_worker(path: Path) -> list[str]:
    if not path.is_file():
        raise PyVRPNextRuntimeError(
            f"Configured experimental PyVRP worker does not exist: {path}"
        )

    if path.suffix.lower() == ".py":
        if getattr(sys, "frozen", False):
            raise PyVRPNextRuntimeError(
                "A .py experimental worker cannot be launched by the frozen "
                "application. Configure the built worker EXE instead."
            )
        return [sys.executable, str(path)]

    return [str(path)]


def _source_worker_command() -> list[str] | None:
    python_exe = PROJECT_DIR / ".venv-pyvrp-next" / "Scripts" / "python.exe"
    worker_py = PROJECT_DIR / "pyvrp_next_worker.py"
    if python_exe.is_file() and worker_py.is_file():
        return [str(python_exe), str(worker_py)]
    return None


def _dist_worker_candidates() -> list[Path]:
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        worker_root = Path(sys.executable).resolve().parent / "pyvrp-next"
    else:
        # The repository currently stores dist next to bizant_source.
        worker_root = PROJECT_DIR.parent / "dist" / "pyvrp-next"

    # Prefer the onedir build.  Unlike a one-file PyInstaller executable it
    # does not unpack more than 100 MB of Python/native libraries for every
    # parallel solve process.  Keep the old flat path as a compatibility
    # fallback for existing installations.
    candidates.extend(
        [
            worker_root / Path(WORKER_FILENAME).stem / WORKER_FILENAME,
            worker_root / WORKER_FILENAME,
        ]
    )
    if not getattr(sys, "frozen", False):
        conventional_root = PROJECT_DIR / "dist" / "pyvrp-next"
        candidates.extend(
            [
                conventional_root / Path(WORKER_FILENAME).stem / WORKER_FILENAME,
                conventional_root / WORKER_FILENAME,
            ]
        )

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _resolve_worker_command(config: Any) -> list[str]:
    """Resolve the worker without ever falling back to the stable interpreter."""
    explicit = _normalise_path(getattr(config, "pyvrp_next_worker_path", ""))
    if explicit is not None:
        return _command_for_explicit_worker(explicit)

    source_command = _source_worker_command()
    if source_command is not None:
        return source_command

    candidates = _dist_worker_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]

    checked = [
        str(PROJECT_DIR / ".venv-pyvrp-next" / "Scripts" / "python.exe"),
        *(str(path) for path in candidates),
    ]
    raise PyVRPNextRuntimeError(
        "Experimental PyVRP 0.14 worker is not installed. Checked: "
        + "; ".join(checked)
        + ". Run setup_pyvrp_next.py with a compatible 0.14 wheel, then "
          "build_pyvrp_next_worker.py, or configure pyvrp_next_worker_path."
    )


def _worker_timeout_seconds(config: Any) -> float:
    configured = getattr(config, "pyvrp_next_worker_timeout_seconds", 0)
    try:
        configured_value = float(configured or 0)
    except (TypeError, ValueError) as exc:
        raise PyVRPNextRuntimeError(
            "pyvrp_next_worker_timeout_seconds must be a number"
        ) from exc

    if configured_value > 0:
        return configured_value

    try:
        solve_limit = float(getattr(config, "time_limit_seconds", 0) or 0)
    except (TypeError, ValueError):
        solve_limit = 0
    return max(
        DEFAULT_WORKER_OVERHEAD_SECONDS,
        solve_limit + DEFAULT_WORKER_OVERHEAD_SECONDS,
    )


def _worker_environment() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # The worker writes PyVRP's progress table to a pipe.  Without unbuffered
    # output those lines may remain hidden until the solve has finished.
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("_MEIPASS2", None)
    return env


def _hidden_process_kwargs() -> Dict[str, Any]:
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _run_process(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_worker_environment(),
            cwd=str(PROJECT_DIR),
            **_hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise PyVRPNextRuntimeError(
            f"Experimental PyVRP worker timed out after {timeout:g} seconds"
        ) from exc
    except OSError as exc:
        raise PyVRPNextRuntimeError(
            f"Could not start experimental PyVRP worker: {exc}"
        ) from exc


def _run_process_streaming(
    command: Sequence[str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run the solve worker while forwarding each output line to our logger."""
    command_list = list(command)
    output_tail = deque(maxlen=250)
    try:
        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=_worker_environment(),
            cwd=str(PROJECT_DIR),
            **_hidden_process_kwargs(),
        )
    except OSError as exc:
        raise PyVRPNextRuntimeError(
            f"Could not start experimental PyVRP worker: {exc}"
        ) from exc

    def forward_output() -> None:
        if process.stdout is None:
            return
        previous_line: str | None = None
        repeated = 0

        def flush_repeated() -> None:
            nonlocal repeated
            if not repeated:
                return
            summary = f"Previous progress line repeated {repeated} more times."
            output_tail.append(summary)
            logger.info("[PyVRP 0.14] %s", summary)
            repeated = 0

        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            if line == previous_line:
                repeated += 1
                continue

            flush_repeated()
            output_tail.append(line)
            logger.info("[PyVRP 0.14] %s", line)
            previous_line = line

        flush_repeated()

    reader = threading.Thread(
        target=forward_output,
        name=f"pyvrp-next-output-{process.pid}",
        daemon=True,
    )
    reader.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            try:
                process.kill()
            except OSError:
                # The worker may have exited in the small race between wait()
                # timing out and kill().  Preserve the intended timeout error.
                pass
        finally:
            try:
                process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        raise PyVRPNextRuntimeError(
            f"Experimental PyVRP worker timed out after {timeout:g} seconds"
        ) from exc
    finally:
        reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()

    return subprocess.CompletedProcess(
        command_list,
        return_code,
        stdout="\n".join(output_tail),
        stderr="",
    )


def _parse_self_check(stdout: str) -> Dict[str, Any]:
    # A frozen executable may emit a harmless bootstrap line before the JSON.
    for line in reversed((stdout or "").splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise PyVRPNextRuntimeError(
        "Experimental PyVRP worker did not return valid self-check JSON"
    )


def _validate_worker_metadata(metadata: Any, *, context: str) -> str:
    if not isinstance(metadata, dict):
        raise PyVRPNextRuntimeError(f"Invalid {context} metadata from experimental worker")
    if metadata.get("protocol_version") != PROTOCOL_VERSION:
        raise PyVRPNextRuntimeError(
            f"Experimental worker protocol mismatch during {context}: "
            f"expected {PROTOCOL_VERSION}, got {metadata.get('protocol_version')!r}"
        )
    if metadata.get("solver_backend") != WORKER_PROTOCOL_BACKEND:
        raise PyVRPNextRuntimeError(
            f"Unexpected experimental worker backend during {context}: "
            f"{metadata.get('solver_backend')!r}"
        )

    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, Mapping):
        capabilities = {}
    missing_capabilities = sorted(
        capability
        for capability in REQUIRED_WORKER_CAPABILITIES
        if capabilities.get(capability) is not True
    )
    if missing_capabilities:
        raise PyVRPNextRuntimeError(
            "Experimental worker is missing required capability/capabilities during "
            f"{context}: {', '.join(missing_capabilities)}. Rebuild the PyVRP 0.14 "
            "worker from the current source before using mandatory customers."
        )

    version = str(metadata.get("solver_version") or "").strip()
    if not version.startswith(REQUIRED_PYVRP_VERSION_PREFIX):
        raise PyVRPNextRuntimeError(
            "Experimental worker must use PyVRP 0.14.*, "
            f"but reported {version or 'no version'} during {context}"
        )
    return version


def _self_check(command: Sequence[str], timeout: float) -> str:
    # Startup checks should fail quickly, while still allowing a slow one-file
    # extraction on the first launch.
    check_timeout = min(timeout, 60.0)
    completed = _run_process([*command, "--self-check"], check_timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PyVRPNextRuntimeError(
            "Experimental PyVRP worker self-check failed"
            + (f": {detail}" if detail else f" with exit code {completed.returncode}")
        )

    metadata = _parse_self_check(completed.stdout)
    if metadata.get("kind") != "self_check" or metadata.get("ok") is not True:
        raise PyVRPNextRuntimeError(
            f"Experimental PyVRP worker returned an invalid self-check: {metadata!r}"
        )
    return _validate_worker_metadata(metadata, context="self-check")


def _atomic_pickle_dump(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as file_handle:
            pickle.dump(value, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, path)
    except Exception as exc:
        raise PyVRPNextRuntimeError(
            f"Could not write experimental worker request: {exc}"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _pickle_load(path: Path) -> Any:
    try:
        with path.open("rb") as file_handle:
            return pickle.load(file_handle)
    except Exception as exc:
        raise PyVRPNextRuntimeError(
            f"Could not read experimental worker result: {exc}"
        ) from exc


def _stable_pyvrp_version() -> str:
    try:
        return importlib.metadata.version("pyvrp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _annotate_solution(
    solution: Any,
    *,
    version: str,
    fallback_used: bool,
    fallback_reason: str = "",
) -> Any:
    if solution is None:
        return None

    existing_used = str(getattr(solution, "solver_used", "") or "").strip()
    if fallback_used and existing_used == PUBLIC_SOLVER_BACKEND:
        existing_used = "pyvrp"
    actual_backend = existing_used or (
        "pyvrp" if fallback_used else PUBLIC_SOLVER_BACKEND
    )
    existing_fallback = bool(getattr(solution, "solver_fallback_used", False))
    existing_reason = str(getattr(solution, "solver_fallback_reason", "") or "")

    # Preserve an exact OR-Tools fallback performed inside the adapter.  The
    # sidecar's PyVRP version is still useful as runtime provenance, but it is
    # not reported as the version of an OR-Tools-produced solution.
    setattr(solution, "solver_requested", PUBLIC_SOLVER_BACKEND)
    setattr(solution, "solver_used", actual_backend)
    setattr(solution, "solver_backend", actual_backend)
    setattr(solution, "solver_actual_backend", actual_backend)
    setattr(solution, "solver_runtime_version", version)
    setattr(
        solution,
        "solver_version",
        version if actual_backend.startswith("pyvrp") else "",
    )
    setattr(solution, "solver_fallback_used", bool(fallback_used or existing_fallback))
    setattr(solution, "solver_fallback_reason", fallback_reason or existing_reason)
    return solution


def _solve_with_stable_fallback(
    allocation: Any,
    depot_location: Any,
    distance_matrix: Any,
    config: Any,
    location_config: Any,
    vehicle_configs: Any,
    reason: str,
) -> Any:
    from pyvrp_solver import solve_cvrp_pyvrp

    logger.warning(
        "Experimental PyVRP failed; using the explicitly enabled stable fallback: %s",
        reason,
    )
    stable_config = copy.copy(config)
    stable_config.solver_type = "pyvrp"
    solution = solve_cvrp_pyvrp(
        allocation=allocation,
        depot_location=depot_location,
        distance_matrix=distance_matrix,
        config=stable_config,
        location_config=location_config,
        vehicle_configs=vehicle_configs,
    )
    validate_mandatory_customer_solution(
        allocation,
        solution,
        context="stable PyVRP fallback result",
    )
    return _annotate_solution(
        solution,
        version=_stable_pyvrp_version(),
        fallback_used=True,
        fallback_reason=reason,
    )


def _solve_experimental(
    allocation: Any,
    depot_location: Any,
    distance_matrix: Any,
    config: Any,
    location_config: Any,
    vehicle_configs: Any,
) -> Any:
    command = _resolve_worker_command(config)
    timeout = _worker_timeout_seconds(config)
    configured_timeout = float(
        getattr(config, "pyvrp_next_worker_timeout_seconds", 0) or 0
    )
    logger.info(
        "Experimental PyVRP worker process timeout: %.0fs (%s); "
        "solver search limit: %ss",
        timeout,
        "explicit" if configured_timeout > 0 else "automatic",
        getattr(config, "time_limit_seconds", 0),
    )

    request = {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "solve_request",
        "solver_backend": WORKER_PROTOCOL_BACKEND,
        "required_version_prefix": REQUIRED_PYVRP_VERSION_PREFIX,
        "required_capabilities": sorted(REQUIRED_WORKER_CAPABILITIES),
        "payload": {
            "allocation": allocation,
            "depot_location": depot_location,
            "distance_matrix": distance_matrix,
            "config": config,
            "location_config": location_config,
            "vehicle_configs": vehicle_configs,
        },
    }

    with tempfile.TemporaryDirectory(prefix="cvrp_pyvrp_next_") as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / "request.pkl"
        output_path = temp_path / "result.pkl"
        _atomic_pickle_dump(input_path, request)

        completed = _run_process_streaming(
            [*command, str(input_path), str(output_path)],
            timeout,
        )

        envelope = _pickle_load(output_path) if output_path.is_file() else None
        if completed.returncode != 0:
            worker_error = ""
            if isinstance(envelope, dict):
                worker_error = str(envelope.get("error") or "").strip()
            detail = worker_error or (completed.stderr or completed.stdout or "").strip()
            if isinstance(envelope, dict) and envelope.get("error_type") == "MandatoryVisitInvariantError":
                raise MandatoryVisitInvariantError(
                    "Experimental PyVRP worker rejected an invalid mandatory-customer solution: "
                    + (detail or "unknown mandatory-customer invariant error")
                )
            raise PyVRPNextRuntimeError(
                "Experimental PyVRP worker failed"
                + (f": {detail}" if detail else f" with exit code {completed.returncode}")
            )

        if envelope is None:
            raise PyVRPNextRuntimeError(
                "Experimental PyVRP worker exited successfully but created no result file"
            )
        if not isinstance(envelope, dict):
            raise PyVRPNextRuntimeError("Experimental worker result is not an envelope")
        if envelope.get("kind") != "solve_result":
            raise PyVRPNextRuntimeError(
                f"Unexpected experimental worker result kind: {envelope.get('kind')!r}"
            )

        # The worker validates its installed PyVRP version before importing the
        # solver, and the parent validates the signed protocol envelope here.
        # A separate pre-flight process would double every worker startup and,
        # with parallel one-file builds, could spend over a minute only
        # extracting files before the actual solve even begins.
        result_version = _validate_worker_metadata(envelope, context="solve result")
        if envelope.get("ok") is not True:
            raise PyVRPNextRuntimeError(
                "Experimental PyVRP worker rejected the solve: "
                + str(envelope.get("error") or "unknown worker error")
            )
        if "solution" not in envelope:
            raise PyVRPNextRuntimeError("Experimental worker result has no solution field")

        validate_mandatory_customer_solution(
            allocation,
            envelope["solution"],
            context="experimental PyVRP parent result validation",
        )

        return _annotate_solution(
            envelope["solution"],
            version=result_version,
            fallback_used=False,
        )


def solve_cvrp_pyvrp_next(
    allocation: Any,
    depot_location: Any,
    distance_matrix: Any,
    config: Any,
    location_config: Any,
    vehicle_configs: Any,
) -> Any:
    """Solve with process-isolated PyVRP 0.14, optionally falling back to 0.13.

    Stable fallback is opt-in through ``pyvrp_next_fallback_to_stable``.  Any
    installation, protocol, version, timeout, or worker solve error otherwise
    fails loudly so an experimental run can never be mistaken for a stable one.
    """
    try:
        return _solve_experimental(
            allocation,
            depot_location,
            distance_matrix,
            config,
            location_config,
            vehicle_configs,
        )
    except PyVRPNextRuntimeError as exc:
        if not bool(getattr(config, "pyvrp_next_fallback_to_stable", False)):
            raise
        return _solve_with_stable_fallback(
            allocation,
            depot_location,
            distance_matrix,
            config,
            location_config,
            vehicle_configs,
            reason=str(exc),
        )


__all__ = [
    "PROTOCOL_VERSION",
    "PUBLIC_SOLVER_BACKEND",
    "HARD_MANDATORY_CUSTOMERS_CAPABILITY",
    "REQUIRED_WORKER_CAPABILITIES",
    "PyVRPNextRuntimeError",
    "MandatoryVisitInvariantError",
    "mandatory_visit_fingerprint",
    "validate_mandatory_customer_solution",
    "solve_cvrp_pyvrp_next",
]
