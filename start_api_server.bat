@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo CVRP Optimizer - API Server
echo.
echo Default listen address is configured in config.py / Settings.
echo Use 0.0.0.0 to accept requests from other computers.
echo.

if exist "%APP_DIR%Bizant.exe" (
    "%APP_DIR%Bizant.exe" --server
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    "%APP_DIR%.venv\Scripts\python.exe" "%APP_DIR%cvrp_api_server.py"
) else (
    where python.exe >nul 2>nul
    if not errorlevel 1 (
        python.exe "%APP_DIR%cvrp_api_server.py"
        exit /b %errorlevel%
    )

    where py.exe >nul 2>nul
    if not errorlevel 1 (
        py.exe "%APP_DIR%cvrp_api_server.py"
        exit /b %errorlevel%
    )

    echo Python not found.
    echo Install Python or build/copy Bizant.exe first.
    pause
    exit /b 1
)
