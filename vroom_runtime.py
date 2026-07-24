"""Process-isolated bridge to the VROOM companion worker."""

from __future__ import annotations

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
SOLVER_BACKEND = "vroom"
REQUIRED_PYVROOM_VERSION_PREFIX = "1.15"
PROJECT_DIR = Path(__file__).resolve().parent
WORKER_FILENAME = "CVRP_VROOM_Worker.exe"
logger = logging.getLogger(__name__)


class VROOMRuntimeError(RuntimeError):
    """Raised when the VROOM worker is missing or returns an invalid result."""


_SELF_CHECK_LOCK = threading.Lock()
_SELF_CHECK_CACHE: Dict[Tuple[str, ...], str] = {}


def _normalise_path(raw_path: Any) -> Path | None:
    text = os.path.expandvars(os.path.expanduser(str(raw_path or "").strip()))
    return Path(text).resolve() if text else None


def _command_for_explicit_worker(path: Path) -> list[str]:
    if not path.is_file():
        raise VROOMRuntimeError(f"Configured VROOM worker does not exist: {path}")
    if path.suffix.lower() == ".py":
        if getattr(sys, "frozen", False):
            raise VROOMRuntimeError(
                "A .py VROOM worker cannot be launched by the frozen application. "
                "Configure CVRP_VROOM_Worker.exe instead."
            )
        return [sys.executable, str(path)]
    return [str(path)]


def _source_worker_command() -> list[str] | None:
    worker_py = PROJECT_DIR / "vroom_worker.py"
    python_candidates = [
        PROJECT_DIR / ".venv-vroom" / "Scripts" / "python.exe",
        PROJECT_DIR / ".venv-vroom" / "bin" / "python",
    ]
    for python_exe in python_candidates:
        if python_exe.is_file() and worker_py.is_file():
            return [str(python_exe), str(worker_py)]
    return None


def _dist_worker_candidates() -> list[Path]:
    if getattr(sys, "frozen", False):
        roots = [Path(sys.executable).resolve().parent / "vroom"]
    else:
        roots = [
            PROJECT_DIR.parent / "dist" / "vroom",
            PROJECT_DIR / "dist" / "vroom",
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
    explicit = _normalise_path(getattr(config, "vroom_worker_path", ""))
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
        str(PROJECT_DIR / ".venv-vroom" / "Scripts" / "python.exe"),
        *(str(path) for path in candidates),
    ]
    raise VROOMRuntimeError(
        "VROOM worker is not installed. Checked: "
        + "; ".join(checked)
        + ". Run setup_vroom.py, then build_vroom_worker.py before creating "
          "a distributable build, or configure vroom_worker_path."
    )


def _exploration_level(config: Any) -> int:
    try:
        value = int(getattr(config, "vroom_exploration_level", 5))
    except (TypeError, ValueError) as exc:
        raise VROOMRuntimeError("vroom_exploration_level must be an integer") from exc
    if not 0 <= value <= 5:
        raise VROOMRuntimeError("vroom_exploration_level must be between 0 and 5")
    return value


def _thread_count(config: Any) -> int:
    try:
        configured = int(getattr(config, "vroom_threads", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise VROOMRuntimeError("vroom_threads must be an integer") from exc
    if configured < 0:
        raise VROOMRuntimeError("vroom_threads cannot be negative")
    if configured == 0:
        return max(1, (os.cpu_count() or 2) - 1)
    return configured


def _solve_limit_seconds(config: Any) -> float:
    try:
        value = float(getattr(config, "time_limit_seconds", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise VROOMRuntimeError("time_limit_seconds must be a number") from exc
    return max(0.0, value)


def _process_timeout_seconds(config: Any) -> float:
    try:
        configured = float(getattr(config, "vroom_worker_timeout_seconds", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise VROOMRuntimeError("vroom_worker_timeout_seconds must be a number") from exc
    if configured < 0:
        raise VROOMRuntimeError("vroom_worker_timeout_seconds cannot be negative")
    if configured > 0:
        return configured
    return max(60.0, _solve_limit_seconds(config) + 60.0)


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
        raise VROOMRuntimeError(
            f"VROOM worker timed out after {timeout:g} seconds"
        ) from exc
    except OSError as exc:
        raise VROOMRuntimeError(f"Could not start VROOM worker: {exc}") from exc


def _last_json_object(output: str) -> Dict[str, Any]:
    for line in reversed((output or "").splitlines()):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise VROOMRuntimeError("VROOM worker did not return valid JSON metadata")


def _validate_metadata(metadata: Any, *, context: str) -> str:
    if not isinstance(metadata, dict):
        raise VROOMRuntimeError(f"Invalid VROOM worker metadata during {context}")
    if metadata.get("protocol_version") != PROTOCOL_VERSION:
        raise VROOMRuntimeError(
            f"VROOM worker protocol mismatch during {context}: "
            f"expected {PROTOCOL_VERSION}, got {metadata.get('protocol_version')!r}"
        )
    if metadata.get("solver_backend") != SOLVER_BACKEND:
        raise VROOMRuntimeError(
            f"Unexpected VROOM worker backend during {context}: "
            f"{metadata.get('solver_backend')!r}"
        )
    version = str(metadata.get("solver_version") or "").strip()
    if not version.startswith(REQUIRED_PYVROOM_VERSION_PREFIX):
        raise VROOMRuntimeError(
            f"VROOM worker must use pyvroom {REQUIRED_PYVROOM_VERSION_PREFIX}.*, "
            f"but reported {version or 'no version'} during {context}"
        )
    return version


def _self_check(command: Sequence[str], timeout: float) -> str:
    key = tuple(command)
    with _SELF_CHECK_LOCK:
        cached = _SELF_CHECK_CACHE.get(key)
        if cached:
            return cached

        completed = _run_process([*command, "--self-check"], min(timeout, 60.0))
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise VROOMRuntimeError(
                "VROOM worker self-check failed"
                + (f": {detail}" if detail else f" with exit code {completed.returncode}")
            )
        metadata = _last_json_object(completed.stdout)
        if metadata.get("ok") is not True or metadata.get("kind") != "self_check":
            raise VROOMRuntimeError(f"Invalid VROOM self-check: {metadata!r}")
        version = _validate_metadata(metadata, context="self-check")
        _SELF_CHECK_CACHE[key] = version
        return version


def _write_problem(path: Path, payload: Dict[str, Any]) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    except (OSError, TypeError, ValueError) as exc:
        raise VROOMRuntimeError(f"Could not write VROOM problem JSON: {exc}") from exc


def _read_result(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise VROOMRuntimeError("VROOM worker finished without a result file")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VROOMRuntimeError(f"Could not read VROOM result JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VROOMRuntimeError("VROOM result wrapper is not a JSON object")
    return value


def run_vroom_problem(payload: Dict[str, Any], config: Any) -> tuple[Dict[str, Any], str]:
    """Solve one official VROOM JSON problem and return ``(response, version)``."""
    command = _resolve_worker_command(config)
    timeout = _process_timeout_seconds(config)
    version = _self_check(command, timeout)
    exploration = _exploration_level(config)
    threads = _thread_count(config)
    limit = _solve_limit_seconds(config)

    logger.info(
        "Starting VROOM %s (exploration=%s, threads=%s, limit=%ss)",
        version,
        exploration,
        threads,
        f"{limit:g}",
    )
    with tempfile.TemporaryDirectory(prefix="cvrp_vroom_") as temp_dir:
        input_path = Path(temp_dir) / "problem.json"
        output_path = Path(temp_dir) / "result.json"
        _write_problem(input_path, payload)
        completed = _run_process(
            [
                *command,
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--exploration",
                str(exploration),
                "--threads",
                str(threads),
                "--limit",
                str(limit),
            ],
            timeout,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise VROOMRuntimeError(
                "VROOM worker failed"
                + (f": {detail[-4000:]}" if detail else f" with exit code {completed.returncode}")
            )
        wrapper = _read_result(output_path)

    result_version = _validate_metadata(wrapper, context="solve")
    if result_version != version:
        raise VROOMRuntimeError(
            f"VROOM worker version changed between self-check and solve: "
            f"{version} -> {result_version}"
        )
    result = wrapper.get("result")
    if not isinstance(result, dict):
        raise VROOMRuntimeError("VROOM worker result does not contain an object")
    if int(result.get("code", -1)) != 0:
        raise VROOMRuntimeError(
            f"VROOM rejected the problem (code={result.get('code')}): "
            f"{result.get('error') or 'unknown error'}"
        )

    logger.info(
        "VROOM finished: routes=%s, unassigned=%s, cost=%s",
        len(result.get("routes") or []),
        len(result.get("unassigned") or []),
        (result.get("summary") or {}).get("cost"),
    )
    return result, version


__all__ = ["VROOMRuntimeError", "run_vroom_problem"]
