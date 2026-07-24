"""Build and verify the isolated official VROOM companion worker."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_DIR.parent
DIST_ROOT = Path(os.environ.get("CVRP_DIST_DIR") or WORKSPACE_DIR / "dist").resolve()
BUILD_ROOT = Path(os.environ.get("CVRP_BUILD_DIR") or WORKSPACE_DIR / "build").resolve()
DIST_DIR = DIST_ROOT / "vroom"
BUILD_DIR = BUILD_ROOT / "vroom"
SPEC_DIR = BUILD_DIR / "spec"
WORKER_SCRIPT = PROJECT_DIR / "vroom_worker.py"
WORKER_NAME = "CVRP_VROOM_Worker"
WORKER_PACKAGE_DIR = DIST_DIR / WORKER_NAME
REQUIRED_VERSION_PREFIX = "1.15"


def _require_environment() -> str:
    try:
        version = importlib.metadata.version("pyvroom")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit(
            "pyvroom is not installed. Run this script with "
            ".venv-vroom\\Scripts\\python.exe."
        ) from exc
    if not version.startswith(REQUIRED_VERSION_PREFIX):
        raise SystemExit(
            f"Refusing to build with pyvroom {version}; expected 1.15.*."
        )
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit("PyInstaller is missing. Run setup_vroom.py first.")
    if not WORKER_SCRIPT.is_file():
        raise SystemExit(f"Worker source is missing: {WORKER_SCRIPT}")
    return version


def _build_command() -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--console",
        "--name",
        WORKER_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "work"),
        "--specpath",
        str(SPEC_DIR),
        "--paths",
        str(PROJECT_DIR),
        "--collect-all",
        "vroom",
        "--copy-metadata",
        "pyvroom",
        str(WORKER_SCRIPT),
    ]
    return command


def _last_json_object(output: str) -> dict:
    for line in reversed((output or "").splitlines()):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("worker returned no JSON self-check")


def _verify(executable: Path, expected_version: str) -> None:
    completed = subprocess.run(
        [str(executable), "--self-check"],
        cwd=str(PROJECT_DIR),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    metadata = _last_json_object(completed.stdout)
    if (
        metadata.get("ok") is not True
        or metadata.get("kind") != "self_check"
        or metadata.get("protocol_version") != 1
        or metadata.get("solver_backend") != "vroom"
        or metadata.get("solver_version") != expected_version
    ):
        raise RuntimeError(f"Invalid VROOM worker self-check: {metadata!r}")


def main() -> int:
    version = _require_environment()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    executable = DIST_DIR / (f"{WORKER_NAME}.exe" if os.name == "nt" else WORKER_NAME)
    if WORKER_PACKAGE_DIR.exists():
        shutil.rmtree(WORKER_PACKAGE_DIR)
    executable.unlink(missing_ok=True)

    env = os.environ.copy()
    env.setdefault("SOURCE_DATE_EPOCH", "1700000000")
    command = _build_command()
    print(f"Building VROOM worker with pyvroom {version}")
    print("> " + " ".join(command))
    subprocess.check_call(command, cwd=str(PROJECT_DIR), env=env)

    if not executable.is_file():
        raise SystemExit(f"PyInstaller did not create the VROOM worker: {executable}")
    try:
        _verify(executable, version)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise SystemExit(f"Frozen VROOM worker self-check failed: {exc}") from exc

    print(f"VROOM worker created and verified: {executable}")
    print("No config.py files were copied or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
