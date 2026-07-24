"""Create the isolated official pyvroom 1.15 Windows environment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv-vroom"
PYVROOM_VERSION = "1.15.2"
PYINSTALLER_VERSION = "6.20.0"


def _run(command: Iterable[str]) -> None:
    command_list = [str(part) for part in command]
    print("> " + " ".join(command_list))
    subprocess.check_call(command_list, cwd=str(PROJECT_DIR))


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _default_base_python() -> str:
    return str(getattr(sys, "_base_executable", "") or sys.executable)


def _verify(python_exe: Path) -> str:
    code = (
        "import importlib.metadata, vroom; "
        "v=importlib.metadata.version('pyvroom'); "
        f"assert v == '{PYVROOM_VERSION}', f'expected {PYVROOM_VERSION}, got {{v}}'; "
        "print(v)"
    )
    completed = subprocess.run(
        [str(python_exe), "-c", code],
        cwd=str(PROJECT_DIR),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    version = completed.stdout.strip().splitlines()[-1]

    worker = PROJECT_DIR / "vroom_worker.py"
    if not worker.is_file():
        raise SystemExit(f"VROOM worker source is missing: {worker}")
    check = subprocess.run(
        [str(python_exe), str(worker), "--self-check"],
        cwd=str(PROJECT_DIR),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    metadata = None
    for line in reversed(check.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            metadata = value
            break
    if not metadata or (
        metadata.get("ok") is not True
        or metadata.get("solver_backend") != "vroom"
        or metadata.get("solver_version") != version
    ):
        raise SystemExit(f"VROOM worker self-check failed: {metadata!r}")
    return version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install pyvroom in an isolated .venv-vroom environment."
    )
    parser.add_argument(
        "--python",
        default=_default_base_python(),
        help="Base Python interpreter used to create .venv-vroom.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base_python = Path(args.python).expanduser().resolve()
    if not base_python.is_file():
        raise SystemExit(f"Base Python does not exist: {base_python}")

    if not _venv_python().is_file():
        print(f"Creating isolated VROOM environment: {VENV_DIR}")
        _run([str(base_python), "-m", "venv", str(VENV_DIR)])

    python_exe = _venv_python()
    if not python_exe.is_file():
        raise SystemExit(f"Virtual environment did not create Python: {python_exe}")

    _run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"])
    _run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--only-binary=:all:",
            f"pyvroom=={PYVROOM_VERSION}",
            f"pyinstaller=={PYINSTALLER_VERSION}",
        ]
    )
    version = _verify(python_exe)
    print(f"VROOM environment ready: pyvroom {version}")
    print(f"Python: {python_exe}")
    print(f'Next build step: "{python_exe}" build_vroom_worker.py')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
