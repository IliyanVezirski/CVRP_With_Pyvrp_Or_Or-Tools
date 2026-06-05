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

echo.
echo To watch first-build progress, run:
echo docker logs -f cvrp_valhalla
echo.
pause
