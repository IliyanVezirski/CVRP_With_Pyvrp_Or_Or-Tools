@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="
echo CVRP Optimizer - Starting...
echo.

if exist "%APP_DIR%Bizant.exe" (
    set "APP_MODE=exe"
    set "APP_CMD=%APP_DIR%Bizant.exe"
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
