@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist "%APP_DIR%CVRP_Optimizer.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path '%APP_DIR%logs' | Out-Null; Start-Process -FilePath '%APP_DIR%CVRP_Optimizer.exe' -ArgumentList '--server' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%APP_DIR%logs\api_server_stdout.log' -RedirectStandardError '%APP_DIR%logs\api_server_stderr.log'"
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path '%APP_DIR%logs' | Out-Null; Start-Process -FilePath '%APP_DIR%.venv\Scripts\python.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%APP_DIR%logs\api_server_stdout.log' -RedirectStandardError '%APP_DIR%logs\api_server_stderr.log'"
) else if exist "%APP_DIR%.venv\Scripts\pythonw.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path '%APP_DIR%logs' | Out-Null; Start-Process -FilePath '%APP_DIR%.venv\Scripts\pythonw.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
) else (
    where python.exe >nul 2>nul
    if not errorlevel 1 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path '%APP_DIR%logs' | Out-Null; Start-Process -FilePath 'python.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%APP_DIR%logs\api_server_stdout.log' -RedirectStandardError '%APP_DIR%logs\api_server_stderr.log'"
        exit /b 0
    )

    where pythonw.exe >nul 2>nul
    if not errorlevel 1 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'pythonw.exe' -ArgumentList '\"%APP_DIR%cvrp_api_server.py\"' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden"
        exit /b 0
    )

    echo Python not found.
    echo Install Python or build CVRP_Optimizer.exe first.
    pause
    exit /b 1
)
