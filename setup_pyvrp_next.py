"""Install pinned stable PyVRP 0.14.0 in the isolated ``.venv-pyvrp-next``.

Examples::

    python setup_pyvrp_next.py
    python setup_pyvrp_next.py --wheel wheels\\pyvrp-0.14.0-cp314-cp314-win_amd64.whl
    python setup_pyvrp_next.py --source-url https://github.com/PyVRP/PyVRP.git \
        --source-commit 0123456789abcdef0123456789abcdef01234567

The default installs the pinned release wheel from PyPI, never a prerelease.
Source installation is never implicit and
requires an exact commit hash.  This script creates/updates only
``.venv-pyvrp-next`` and never installs anything into the production ``.venv``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable
from pyvrp_next_version import PYVRP_NEXT_VERSION


PROJECT_DIR = Path(__file__).resolve().parent
NEXT_VENV_DIR = PROJECT_DIR / ".venv-pyvrp-next"
PYVRP_REQUIREMENT = re.compile(r"^\s*pyvrp(?:\[|\s|[<>=!~@])", re.IGNORECASE)
COMMIT_HASH = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _run(command: Iterable[str]) -> None:
    command_list = [str(part) for part in command]
    print("> " + " ".join(command_list))
    subprocess.check_call(command_list, cwd=str(PROJECT_DIR))


def _venv_python() -> Path:
    if os.name == "nt":
        return NEXT_VENV_DIR / "Scripts" / "python.exe"
    return NEXT_VENV_DIR / "bin" / "python"


def _default_base_python() -> str:
    base = str(getattr(sys, "_base_executable", "") or "").strip()
    return base or sys.executable


def _write_filtered_requirements(destination: Path) -> None:
    source = PROJECT_DIR / "requirements.txt"
    if not source.is_file():
        destination.write_text("", encoding="utf-8")
        return

    kept: list[str] = []
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and PYVRP_REQUIREMENT.match(stripped):
            continue
        kept.append(line)
    destination.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _verify_environment(python_exe: Path) -> str:
    code = (
        "import importlib.metadata, pyvrp; "
        "v=importlib.metadata.version('pyvrp'); "
        f"assert v == {PYVRP_NEXT_VERSION!r}, f'expected stable PyVRP {PYVRP_NEXT_VERSION}, got {{v}}'; "
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
    return completed.stdout.strip().splitlines()[-1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Install isolated stable PyVRP {PYVRP_NEXT_VERSION} (pinned)."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wheel", help=f"Optional local PyVRP {PYVRP_NEXT_VERSION} wheel.")
    source.add_argument(
        "--source-url",
        help="Explicit Git repository URL. Requires --source-commit.",
    )
    parser.add_argument(
        "--source-commit",
        help="Exact 7-40 character hexadecimal commit for source installation.",
    )
    parser.add_argument(
        "--python",
        default=_default_base_python(),
        help="Base Python used to create .venv-pyvrp-next.",
    )
    parser.add_argument(
        "--skip-project-requirements",
        action="store_true",
        help="Install only PyVRP and PyInstaller (advanced/debug use).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    wheel = Path(args.wheel).expanduser().resolve() if args.wheel else None
    if args.source_commit and not args.source_url:
        raise SystemExit("--source-commit is valid only with --source-url")

    if wheel is not None:
        if not wheel.is_file() or wheel.suffix.lower() != ".whl":
            raise SystemExit(f"Wheel does not exist or is not a .whl file: {wheel}")
        if not wheel.name.lower().startswith(f"pyvrp-{PYVRP_NEXT_VERSION}-"):
            raise SystemExit(f"Only stable PyVRP {PYVRP_NEXT_VERSION} wheels are supported: {wheel.name}")
        install_target = str(wheel)
        install_description = f"wheel {wheel.name}"
    elif args.source_url:
        commit = str(args.source_commit or "").strip()
        if not COMMIT_HASH.fullmatch(commit):
            raise SystemExit(
                "Source mode requires --source-commit with an exact 7-40 "
                "character hexadecimal commit hash (branches such as main are rejected)."
            )
        source_url = str(args.source_url).strip()
        if not source_url:
            raise SystemExit("--source-url cannot be empty")
        git_url = source_url if source_url.startswith("git+") else f"git+{source_url}"
        install_target = f"{git_url}@{commit}"
        install_description = f"source commit {commit}"
    else:
        install_target = f"pyvrp=={PYVRP_NEXT_VERSION}"
        install_description = f"PyPI release {PYVRP_NEXT_VERSION}"

    base_python = Path(args.python).expanduser().resolve()
    if not base_python.is_file():
        raise SystemExit(f"Base Python does not exist: {base_python}")

    production_venv = (PROJECT_DIR / ".venv").resolve()
    if NEXT_VENV_DIR.resolve() == production_venv:
        raise SystemExit("Refusing to use the production .venv for PyVRP 0.14")

    if not _venv_python().is_file():
        print(f"Creating isolated environment: {NEXT_VENV_DIR}")
        _run([str(base_python), "-m", "venv", str(NEXT_VENV_DIR)])

    python_exe = _venv_python()
    if not python_exe.is_file():
        raise SystemExit(f"Virtual environment did not create Python: {python_exe}")

    _run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

    if not args.skip_project_requirements:
        with tempfile.TemporaryDirectory(prefix="pyvrp_next_setup_") as temp_dir:
            filtered = Path(temp_dir) / "requirements-without-pyvrp.txt"
            _write_filtered_requirements(filtered)
            if filtered.read_text(encoding="utf-8").strip():
                _run([str(python_exe), "-m", "pip", "install", "-r", str(filtered)])

    print(f"Installing stable PyVRP from {install_description}")
    install = [str(python_exe), "-m", "pip", "install", "--upgrade"]
    if not args.source_url:
        install.append("--only-binary=pyvrp")
    # The extra requirement constrains metadata too, including explicit source
    # builds/local wheels. A conflicting alpha is rejected by pip's resolver.
    install.extend(dict.fromkeys([install_target, f"pyvrp=={PYVRP_NEXT_VERSION}"]))
    _run(install)
    _run([str(python_exe), "-m", "pip", "install", "--upgrade", "pyinstaller"])

    version = _verify_environment(python_exe)
    print(f"Stable 0.14 environment ready: PyVRP {version}")
    print(f"Python: {python_exe}")
    print(
        "Next: run \""
        + str(python_exe)
        + "\" build_pyvrp_next_worker.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
