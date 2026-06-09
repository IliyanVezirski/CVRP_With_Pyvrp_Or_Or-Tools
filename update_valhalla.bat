@echo off
setlocal

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

echo CVRP Optimizer - Valhalla daily update
echo.
echo This will stop cvrp_valhalla, clean C:\valhalla, download fresh Bulgaria OSM data,
echo rebuild Valhalla tiles and start the same named container again.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%start_valhalla.ps1" -UpdateMap

if errorlevel 1 (
    echo.
    echo Valhalla update failed.
    pause
    exit /b 1
)

echo.
echo Valhalla update command finished.
echo.

exit /b 0
