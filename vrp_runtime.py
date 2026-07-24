"""Process-isolated bridge to the reinterpretcat/vrp companion worker."""

from __future__ import annotations

from collections import deque
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from typing import Any, Dict, Sequence, Tuple


PROTOCOL_VERSION = 1
SOLVER_BACKEND = "vrp"
REQUIRED_VERSION_PREFIX = "1.24"
PROJECT_DIR = Path(__file__).resolve().parent
WORKER_FILENAME = "CVRP_VRP_Rust_Worker.exe"
logger = logging.getLogger(__name__)


class VRPRuntimeError(RuntimeError):
    """Raised when the VRP-Rust worker is missing or returns invalid data."""


_SELF_CHECK_LOCK = threading.Lock()
_SELF_CHECK_CACHE: Dict[Tuple[str, ...], str] = {}


def _normalise_path(raw_path: Any) -> Path | None:
    text = os.path.expandvars(os.path.expanduser(str(raw_path or "").strip()))
    return Path(text).resolve() if text else None


def _source_python_candidates() -> list[Path]:
    return [
        PROJECT_DIR / ".venv-vrp-rust" / "Scripts" / "python.exe",
        PROJECT_DIR / ".venv-vrp-rust" / "bin" / "python",
    ]


def _command_for_explicit_worker(path: Path) -> list[str]:
    if not path.is_file():
        raise VRPRuntimeError(f"Configured VRP-Rust worker does not exist: {path}")
    if path.suffix.lower() != ".py":
        return [str(path)]
    if getattr(sys, "frozen", False):
        raise VRPRuntimeError(
            "A .py VRP-Rust worker cannot be launched by the frozen application. "
            "Configure CVRP_VRP_Rust_Worker.exe instead."
        )
    for python_exe in _source_python_candidates():
        if python_exe.is_file():
            return [str(python_exe), str(path)]
    raise VRPRuntimeError(
        "A Python VRP-Rust worker requires the isolated .venv-vrp-rust runtime."
    )


def _source_worker_command() -> list[str] | None:
    worker_py = PROJECT_DIR / "vrp_rust_worker.py"
    for python_exe in _source_python_candidates():
        if python_exe.is_file() and worker_py.is_file():
            return [str(python_exe), str(worker_py)]
    return None


def _dist_worker_candidates() -> list[Path]:
    if getattr(sys, "frozen", False):
        roots = [Path(sys.executable).resolve().parent / "vrp-rust"]
    else:
        roots = [
            PROJECT_DIR.parent / "dist" / "vrp-rust",
            PROJECT_DIR / "dist" / "vrp-rust",
        ]

    candidates: list[Path] = []
    for root in roots:
        candidates.extend(
            [
                root / Path(WORKER_FILENAME).stem / WORKER_FILENAME,
                root / WORKER_FILENAME,
            ]
        )
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _resolve_worker_command(config: Any) -> list[str]:
    explicit = _normalise_path(getattr(config, "vrp_worker_path", ""))
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
        str(PROJECT_DIR / ".venv-vrp-rust" / "Scripts" / "python.exe"),
        *(str(path) for path in candidates),
    ]
    raise VRPRuntimeError(
        "VRP-Rust worker is not installed. Checked: "
        + "; ".join(checked)
        + ". Run setup_vrp_rust.py for source use, or configure vrp_worker_path."
    )


def vrp_thread_count(config: Any) -> int:
    try:
        configured = int(getattr(config, "vrp_threads", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise VRPRuntimeError("vrp_threads must be an integer") from exc
    if configured < 0 or configured > 256:
        raise VRPRuntimeError("vrp_threads must be between 0 and 256")
    return configured or max(1, os.cpu_count() or 1)


def vrp_max_generations(config: Any) -> int:
    try:
        value = int(getattr(config, "vrp_max_generations", 1_000_000) or 1_000_000)
    except (TypeError, ValueError) as exc:
        raise VRPRuntimeError("vrp_max_generations must be an integer") from exc
    if not 1 <= value <= 1_000_000_000_000:
        raise VRPRuntimeError(
            "vrp_max_generations must be between 1 and 1000000000000"
        )
    return value


def _solve_limit_seconds(config: Any) -> float:
    try:
        value = float(getattr(config, "time_limit_seconds", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise VRPRuntimeError("time_limit_seconds must be a number") from exc
    return max(1.0, value)


def _process_timeout_seconds(config: Any) -> float:
    try:
        configured = float(getattr(config, "vrp_worker_timeout_seconds", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise VRPRuntimeError("vrp_worker_timeout_seconds must be a number") from exc
    if configured < 0:
        raise VRPRuntimeError("vrp_worker_timeout_seconds cannot be negative")
    return configured if configured > 0 else max(60.0, _solve_limit_seconds(config) + 180.0)


def _worker_environment() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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


def _last_json_object(output: str) -> Dict[str, Any]:
    for line in reversed((output or "").splitlines()):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise VRPRuntimeError("VRP-Rust worker did not return valid JSON metadata")


def _validate_metadata(metadata: Any, *, context: str) -> str:
    if not isinstance(metadata, dict):
        raise VRPRuntimeError(f"Invalid VRP-Rust worker metadata during {context}")
    if metadata.get("protocol_version") != PROTOCOL_VERSION:
        raise VRPRuntimeError(
            f"VRP-Rust worker protocol mismatch during {context}: "
            f"expected {PROTOCOL_VERSION}, got {metadata.get('protocol_version')!r}"
        )
    if metadata.get("solver_backend") != SOLVER_BACKEND:
        raise VRPRuntimeError(
            f"Unexpected VRP-Rust backend during {context}: "
            f"{metadata.get('solver_backend')!r}"
        )
    version = str(metadata.get("solver_version") or "").strip()
    if not version.startswith(REQUIRED_VERSION_PREFIX):
        raise VRPRuntimeError(
            f"VRP-Rust worker must use vrp-cli {REQUIRED_VERSION_PREFIX}.*, "
            f"but reported {version or 'no version'} during {context}"
        )
    return version


def _self_check(command: Sequence[str], timeout: float) -> str:
    key = tuple(command)
    with _SELF_CHECK_LOCK:
        cached = _SELF_CHECK_CACHE.get(key)
        if cached:
            return cached
        try:
            completed = subprocess.run(
                [*command, "--self-check"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(timeout, 60.0),
                env=_worker_environment(),
                cwd=str(PROJECT_DIR),
                **_hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise VRPRuntimeError("VRP-Rust worker self-check timed out") from exc
        except OSError as exc:
            raise VRPRuntimeError(f"Could not start VRP-Rust worker: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise VRPRuntimeError(
                "VRP-Rust worker self-check failed"
                + (f": {detail[-4000:]}" if detail else f" with exit code {completed.returncode}")
            )
        metadata = _last_json_object(completed.stdout)
        if metadata.get("ok") is not True or metadata.get("kind") != "self_check":
            raise VRPRuntimeError(f"Invalid VRP-Rust self-check: {metadata!r}")
        version = _validate_metadata(metadata, context="self-check")
        _SELF_CHECK_CACHE[key] = version
        return version


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    except (OSError, TypeError, ValueError) as exc:
        raise VRPRuntimeError(f"Could not write VRP-Rust problem JSON: {exc}") from exc


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise VRPRuntimeError("VRP-Rust worker finished without a result file")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VRPRuntimeError(f"Could not read VRP-Rust result JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VRPRuntimeError("VRP-Rust result wrapper is not a JSON object")
    return value


def _run_streaming(
    command: Sequence[str],
    timeout: float,
    *,
    log_progress: bool,
) -> tuple[int, str]:
    lines: deque[str] = deque(maxlen=240)
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_worker_environment(),
            cwd=str(PROJECT_DIR),
            **_hidden_process_kwargs(),
        )
    except OSError as exc:
        raise VRPRuntimeError(f"Could not start VRP-Rust worker: {exc}") from exc

    def consume_output() -> None:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            lines.append(line)
            if log_progress:
                logger.info("[VRP-Rust] %s", line)

    reader = threading.Thread(target=consume_output, name="vrp-rust-log", daemon=True)
    reader.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=10)
        reader.join(timeout=2)
        raise VRPRuntimeError(f"VRP-Rust worker timed out after {timeout:g} seconds") from exc
    reader.join(timeout=5)
    return return_code, "\n".join(lines)


def run_vrp_problem(payload: Dict[str, Any], config: Any) -> tuple[Dict[str, Any], str]:
    """Solve one pragmatic problem and return ``(solution, vrp_cli_version)``."""
    command = _resolve_worker_command(config)
    timeout = _process_timeout_seconds(config)
    version = _self_check(command, timeout)
    log_progress = bool(getattr(config, "vrp_log_progress", True))
    logger.info(
        "Starting VRP-Rust %s (threads=%s, max_generations=%s, limit=%ss)",
        version,
        vrp_thread_count(config),
        vrp_max_generations(config),
        f"{_solve_limit_seconds(config):g}",
    )

    with tempfile.TemporaryDirectory(prefix="cvrp_vrp_rust_") as temp_dir:
        input_path = Path(temp_dir) / "problem.json"
        output_path = Path(temp_dir) / "result.json"
        _write_json(input_path, payload)
        return_code, process_output = _run_streaming(
            [*command, "--input", str(input_path), "--output", str(output_path)],
            timeout,
            log_progress=log_progress,
        )
        wrapper = _read_json(output_path)

    if return_code != 0 or wrapper.get("ok") is not True:
        detail = str(wrapper.get("error") or process_output or "").strip()
        raise VRPRuntimeError(
            "VRP-Rust worker failed"
            + (f": {detail[-4000:]}" if detail else f" with exit code {return_code}")
        )
    result_version = _validate_metadata(wrapper, context="solve")
    if result_version != version:
        raise VRPRuntimeError(
            f"VRP-Rust worker version changed between self-check and solve: "
            f"{version} -> {result_version}"
        )
    result = wrapper.get("solution")
    if not isinstance(result, dict):
        raise VRPRuntimeError("VRP-Rust worker result does not contain a solution object")
    logger.info(
        "VRP-Rust finished: tours=%s, unassigned=%s, cost=%s",
        len(result.get("tours") or []),
        len(result.get("unassigned") or []),
        (result.get("statistic") or {}).get("cost"),
    )
    return result, version


__all__ = [
    "VRPRuntimeError",
    "run_vrp_problem",
    "vrp_max_generations",
    "vrp_thread_count",
]
