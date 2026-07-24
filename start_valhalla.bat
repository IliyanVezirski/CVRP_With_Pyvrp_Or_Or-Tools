@echo off
setlocal

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

echo CVRP Optimizer - Valhalla
echo.
echo This script starts one reusable Docker container named cvrp_valhalla.
echo Valhalla data will be stored in C:\valhalla.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%start_valhalla.ps1"

if errorlevel 1 (
    echo.
    echo Valhalla start failed.
    pause
    exit /b 1
)

exit /b 0
