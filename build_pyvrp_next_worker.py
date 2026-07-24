"""Build the isolated PyVRP 0.14 worker sidecar with PyInstaller.

Run this script with ``.venv-pyvrp-next``.  It refuses every PyVRP version
outside 0.14.*, writes only build artifacts and ``dist/pyvrp-next``, and never
copies or modifies the application's runtime ``config.py``.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_DIR.parent
DIST_ROOT = Path(os.environ.get("CVRP_DIST_DIR") or WORKSPACE_DIR / "dist").resolve()
BUILD_ROOT = Path(os.environ.get("CVRP_BUILD_DIR") or WORKSPACE_DIR / "build").resolve()
DIST_DIR = DIST_ROOT / "pyvrp-next"
BUILD_DIR = BUILD_ROOT / "pyvrp-next"
SPEC_DIR = BUILD_DIR / "spec"
WORKER_SCRIPT = PROJECT_DIR / "pyvrp_next_worker.py"
WORKER_NAME = "CVRP_PyVRP_Next_Worker"
WORKER_PACKAGE_DIR = DIST_DIR / WORKER_NAME
REQUIRED_VERSION_PREFIX = "0.14"


def _require_experimental_environment() -> str:
    try:
        version = importlib.metadata.version("pyvrp")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit(
            "PyVRP is not installed. Run this script with "
            ".venv-pyvrp-next\\Scripts\\python.exe."
        ) from exc

    if not version.startswith(REQUIRED_VERSION_PREFIX):
        raise SystemExit(
            f"Refusing to build with PyVRP {version}. This sidecar requires 0.14.*. "
            "Run it with .venv-pyvrp-next\\Scripts\\python.exe."
        )
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit(
            "PyInstaller is missing from .venv-pyvrp-next. "
            "Run setup_pyvrp_next.py first."
        )
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
    ]

    # PyVRP and OR-Tools contain native extensions and modules discovered at
    # runtime.  Collection happens from this isolated environment only.
    for package in (
        "pyvrp",
        "ortools",
        "google.protobuf",
        "numpy",
        "tqdm",
    ):
        command.extend(["--collect-all", package])

    for module in (
        "pyvrp_solver",
        "pyvrp.stop",
        "pyvrp.stop.MaxRuntime",
        "ortools.constraint_solver.pywrapcp",
        "ortools.constraint_solver.routing_enums_pb2",
    ):
        command.extend(["--hidden-import", module])

    command.append(str(WORKER_SCRIPT))
    return command


def _verify_executable(executable: Path, expected_version: str) -> None:
    """Reject a frozen worker unless it can start and reports PyVRP 0.14."""
    try:
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
        metadata = None
        for line in reversed(completed.stdout.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                metadata = value
                break
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        executable.unlink(missing_ok=True)
        raise SystemExit(f"Frozen worker self-check failed: {exc}") from exc

    if not metadata or (
        metadata.get("ok") is not True
        or metadata.get("kind") != "self_check"
        or metadata.get("protocol_version") != 1
        or metadata.get("solver_backend") != "pyvrp_next"
        or metadata.get("solver_version") != expected_version
        or not expected_version.startswith(REQUIRED_VERSION_PREFIX)
    ):
        executable.unlink(missing_ok=True)
        raise SystemExit(f"Frozen worker returned invalid self-check data: {metadata!r}")

    print(f"Frozen worker self-check passed (PyVRP {expected_version}).")


def main() -> int:
    version = _require_experimental_environment()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    executable = DIST_DIR / (f"{WORKER_NAME}.exe" if os.name == "nt" else WORKER_NAME)

    # Remove the previous onedir package and one-file worker.  The one-file
    # format is required on Windows installations where Smart App Control
    # blocks newly generated unsigned onedir launchers.
    if WORKER_PACKAGE_DIR.exists():
        shutil.rmtree(WORKER_PACKAGE_DIR)
    executable.unlink(missing_ok=True)

    env = os.environ.copy()
    env.setdefault("SOURCE_DATE_EPOCH", "1700000000")
    command = _build_command()
    print(f"Building experimental worker with PyVRP {version}")
    print("> " + " ".join(command))
    subprocess.check_call(command, cwd=str(PROJECT_DIR), env=env)

    if not executable.is_file():
        raise SystemExit(f"PyInstaller completed but worker was not created: {executable}")

    _verify_executable(executable, version)
    print(f"Experimental worker created: {executable}")
    print("No config.py files were copied or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
