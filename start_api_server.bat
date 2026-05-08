@echo off
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
