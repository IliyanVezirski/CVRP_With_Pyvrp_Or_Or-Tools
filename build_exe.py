"""
Build script for CVRP_Optimizer.exe.

The important part is the generated PyInstaller spec. OR-Tools ships Python
modules, protobuf modules and native binaries. A hand-written list of DLL names
breaks easily between OR-Tools versions, so the spec uses PyInstaller hook
helpers such as collect_all(), collect_submodules() and collect_dynamic_libs().
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DIST_DIR = PROJECT_DIR.parent / "dist"
BUILD_DIR = PROJECT_DIR.parent / "build"
SPEC_FILE = PROJECT_DIR / "CVRP_Optimizer.spec"
VERSION_FILE = PROJECT_DIR / "file_version_info.txt"


IMPORT_CHECKS = {
    "PyInstaller": "pyinstaller",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "requests": "requests",
    "numpy": "numpy",
    "folium": "folium",
    "ortools": "ortools",
    "pyvrp": "pyvrp",
    "tqdm": "tqdm",
    "yaml": "pyyaml",
    "matplotlib": "matplotlib",
    "branca": "branca",
}


def _run(command: list[str]) -> None:
    print("> " + " ".join(command))
    subprocess.check_call(command)


def check_dependencies() -> bool:
    """Return True when all build/runtime dependencies are importable."""
    print("Checking Python dependencies...")
    missing: list[str] = []

    for import_name, package_name in IMPORT_CHECKS.items():
        try:
            importlib.import_module(import_name)
            print(f"OK: {package_name}")
        except ImportError:
            print(f"Missing: {package_name}")
            missing.append(package_name)

    if missing:
        print("\nMissing packages: " + ", ".join(sorted(set(missing))))
        return False

    return True


def install_dependencies() -> bool:
    """Install project requirements and PyInstaller in the current interpreter."""
    try:
        requirements = PROJECT_DIR / "requirements.txt"
        if requirements.exists():
            _run([sys.executable, "-m", "pip", "install", "-r", str(requirements)])

        _run([sys.executable, "-m", "pip", "install", "pyinstaller"])
        return True
    except subprocess.CalledProcessError:
        return False


def create_spec_file() -> None:
    """Create a robust PyInstaller spec file for the current environment."""
    project_dir = str(PROJECT_DIR)
    icon_path = PROJECT_DIR / "data" / "icon.ico"

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)
from PyInstaller.utils.win32 import winutils as _pyi_winutils

block_cipher = None

_original_set_exe_build_timestamp = _pyi_winutils.set_exe_build_timestamp
_original_update_exe_pe_checksum = _pyi_winutils.update_exe_pe_checksum


def _safe_set_exe_build_timestamp(exe_path, timestamp):
    try:
        return _original_set_exe_build_timestamp(exe_path, timestamp)
    except OSError as exc:
        if getattr(exc, "errno", None) == 22 or getattr(exc, "winerror", None) == 87:
            print(
                "WARNING: skipping PyInstaller EXE timestamp update "
                f"for {{exe_path!r}} because Windows rejected it: {{exc}}"
            )
            return None
        raise


def _safe_update_exe_pe_checksum(exe_path):
    try:
        return _original_update_exe_pe_checksum(exe_path)
    except OSError as exc:
        if getattr(exc, "errno", None) == 22 or getattr(exc, "winerror", None) == 87:
            print(
                "WARNING: skipping PyInstaller EXE PE checksum update "
                f"for {{exe_path!r}} because Windows rejected it: {{exc}}"
            )
            return None
        raise


_pyi_winutils.set_exe_build_timestamp = _safe_set_exe_build_timestamp
_pyi_winutils.update_exe_pe_checksum = _safe_update_exe_pe_checksum

datas = []
binaries = []
hiddenimports = []


def extend_unique(target, values):
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)


def collect_package(package_name):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package_name)
        extend_unique(datas, pkg_datas)
        extend_unique(binaries, pkg_binaries)
        extend_unique(hiddenimports, pkg_hidden)
    except Exception as exc:
        print(f"WARNING: collect_all({{package_name!r}}) failed: {{exc}}")

    try:
        extend_unique(hiddenimports, collect_submodules(package_name))
    except Exception as exc:
        print(f"WARNING: collect_submodules({{package_name!r}}) failed: {{exc}}")

    try:
        extend_unique(binaries, collect_dynamic_libs(package_name))
    except Exception as exc:
        print(f"WARNING: collect_dynamic_libs({{package_name!r}}) failed: {{exc}}")

    try:
        extend_unique(datas, collect_data_files(package_name))
    except Exception:
        pass


for package in [
    "ortools",
    "google.protobuf",
    "absl",
    "immutabledict",
    "pyvrp",
    "pandas",
    "numpy",
    "openpyxl",
    "folium",
    "branca",
    "jinja2",
    "matplotlib",
    "requests",
    "urllib3",
    "yaml",
    "tqdm",
    "dateutil",
    "tzdata",
]:
    collect_package(package)


explicit_hiddenimports = [
    # Project modules.
    "main",
    "config",
    "config_gui",
    "cvrp_api_server",
    "input_handler",
    "warehouse_manager",
    "cvrp_solver",
    "pyvrp_solver",
    "output_handler",
    "osrm_client",
    "valhalla_client",
    "run_main",

    # OR-Tools modules used by the solver.
    "ortools",
    "ortools.constraint_solver",
    "ortools.constraint_solver.pywrapcp",
    "ortools.constraint_solver.routing_enums_pb2",
    "ortools.constraint_solver.routing_parameters_pb2",
    "ortools.constraint_solver.assignment_pb2",
    "ortools.constraint_solver.search_limit_pb2",
    "ortools.constraint_solver.search_stats_pb2",
    "ortools.constraint_solver.solver_parameters_pb2",
    "ortools.constraint_solver.routing_ils_pb2",
    "ortools.linear_solver",
    "ortools.linear_solver.pywraplp",
    "ortools.linear_solver.linear_solver_pb2",

    # PyVRP modules.
    "pyvrp",
    "pyvrp.stop",
    "pyvrp.stop.MaxRuntime",

    # GUI and output modules.
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "folium.plugins",
    "branca.element",
    "matplotlib.backends.backend_agg",

    # Input/runtime helpers.
    "urllib.request",
    "urllib.error",
    "ssl",
    "json",
    "argparse",
    "http.server",
    "socketserver",
    "multiprocessing",
]
extend_unique(hiddenimports, explicit_hiddenimports)


a = Analysis(
    [r"{str(PROJECT_DIR / 'main_exe.py')}"],
    pathex=[r"{project_dir}"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "notebook",
        "jupyter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CVRP_Optimizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r"{str(icon_path)}" if os.path.exists(r"{str(icon_path)}") else None,
    version=r"{str(VERSION_FILE)}" if os.path.exists(r"{str(VERSION_FILE)}") else None,
)
'''

    SPEC_FILE.write_text(spec_content, encoding="utf-8")
    print(f"Created spec file: {SPEC_FILE}")


def create_version_info() -> None:
    """Create Windows version metadata used by PyInstaller."""
    import datetime

    current_year = datetime.datetime.now().year
    version_info = f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 2, 0, 0),
    prodvers=(1, 2, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'OptioRoute'),
          StringStruct(u'FileDescription', u'CVRP Optimizer'),
          StringStruct(u'FileVersion', u'1.2.0'),
          StringStruct(u'InternalName', u'CVRP_Optimizer'),
          StringStruct(u'LegalCopyright', u'(c) {current_year} OptioRoute'),
          StringStruct(u'OriginalFilename', u'CVRP_Optimizer.exe'),
          StringStruct(u'ProductName', u'CVRP Optimizer'),
          StringStruct(u'ProductVersion', u'1.2.0')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [0x0409, 1200])])
  ]
)
'''
    VERSION_FILE.write_text(version_info, encoding="utf-8")
    print(f"Created version file: {VERSION_FILE}")


def build_exe() -> bool:
    """Run PyInstaller using the generated spec file."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("SOURCE_DATE_EPOCH", "1700000000")

    try:
        _run([
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR),
            str(SPEC_FILE),
        ])
    except subprocess.CalledProcessError as exc:
        print(f"Build failed: {exc}")
        return False

    exe_path = DIST_DIR / "CVRP_Optimizer.exe"
    if not exe_path.exists():
        print(f"Build finished, but EXE was not created: {exe_path}")
        return False

    print(f"EXE created: {exe_path}")
    return True


def create_batch_files() -> None:
    """Create helper batch files in project and dist directories."""
    start_content = r'''@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="
echo CVRP Optimizer - Starting...
echo.

if exist "%APP_DIR%CVRP_Optimizer.exe" (
    set "APP_MODE=exe"
    set "APP_CMD=%APP_DIR%CVRP_Optimizer.exe"
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    set "APP_MODE=python"
    set "APP_CMD=%APP_DIR%.venv\Scripts\python.exe"
) else (
    echo Python virtual environment not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if exist "%APP_DIR%data\input.xlsx" (
    echo Input file found: %APP_DIR%data\input.xlsx
    if "%APP_MODE%"=="exe" (
        "%APP_CMD%" "%APP_DIR%data\input.xlsx"
    ) else (
        "%APP_CMD%" "%APP_DIR%run_main.py" "%APP_DIR%data\input.xlsx"
    )
) else (
    echo Input file not found in data\input.xlsx
    echo Place the input file in data\input.xlsx
    echo or in the current directory as input.xlsx
    echo.
    echo Starting with current configuration...
    if "%APP_MODE%"=="exe" (
        "%APP_CMD%"
    ) else (
        "%APP_CMD%" "%APP_DIR%run_main.py"
    )
)
'''

    settings_content = r'''@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="

if exist "%APP_DIR%CVRP_Optimizer.exe" (
    "%APP_DIR%CVRP_Optimizer.exe" --settings
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    "%APP_DIR%.venv\Scripts\python.exe" "%APP_DIR%config_gui.py"
) else (
    echo Python virtual environment not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
'''

    server_content = r'''@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="

echo CVRP Optimizer - API Server
echo.
echo Default listen address is configured in config.py / Settings.
echo Use 0.0.0.0 to accept requests from other computers.
echo.

if exist "%APP_DIR%CVRP_Optimizer.exe" (
    "%APP_DIR%CVRP_Optimizer.exe" --server
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    "%APP_DIR%.venv\Scripts\python.exe" "%APP_DIR%cvrp_api_server.py"
) else (
    echo Python virtual environment not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
'''

    hidden_server_content = r'''@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="

if exist "%APP_DIR%CVRP_Optimizer.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%APP_DIR%CVRP_Optimizer.exe' -ArgumentList '--server' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
) else if exist "%APP_DIR%.venv\Scripts\pythonw.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%APP_DIR%.venv\Scripts\pythonw.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%APP_DIR%.venv\Scripts\python.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
) else (
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'pythonw.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
        exit /b 0
    )

    where python.exe >nul 2>nul
    if not errorlevel 1 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'python.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
        exit /b 0
    )

    echo Python not found.
    echo Install Python or build CVRP_Optimizer.exe first.
    pause
    exit /b 1
)
'''

    for directory in [PROJECT_DIR, DIST_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "start_cvrp.bat").write_text(start_content, encoding="utf-8-sig", newline="\r\n")
        (directory / "Settings.bat").write_text(settings_content, encoding="utf-8-sig", newline="\r\n")
        (directory / "start_api_server.bat").write_text(server_content, encoding="utf-8-sig", newline="\r\n")
        (directory / "start_api_server_hidden.bat").write_text(hidden_server_content, encoding="utf-8-sig", newline="\r\n")

    print("Created start_cvrp.bat, Settings.bat, start_api_server.bat and start_api_server_hidden.bat")


def copy_runtime_files() -> None:
    """Copy runtime config to the dist folder."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    config_src = PROJECT_DIR / "config.py"
    if config_src.exists():
        shutil.copy2(config_src, DIST_DIR / "config.py")
        print(f"Copied config.py to {DIST_DIR}")

    (DIST_DIR / "data").mkdir(exist_ok=True)


def main() -> None:
    print("CVRP Optimizer - EXE Builder")
    print("=" * 40)
    print(f"Python: {sys.executable}")

    if not check_dependencies():
        answer = input("Install/update dependencies in this Python environment? (y/n): ").strip().lower()
        if answer != "y":
            print("Build cancelled.")
            return
        if not install_dependencies() or not check_dependencies():
            print("Dependencies are still missing. Build cancelled.")
            return

    create_version_info()
    create_spec_file()

    if not build_exe():
        print("EXE build failed. No fallback EXE was created.")
        return

    create_batch_files()
    copy_runtime_files()

    print("\nBuild completed successfully.")
    print(f"EXE: {DIST_DIR / 'CVRP_Optimizer.exe'}")
    print("Place input data in dist\\data\\input.xlsx or edit dist\\config.py.")


if __name__ == "__main__":
    main()
