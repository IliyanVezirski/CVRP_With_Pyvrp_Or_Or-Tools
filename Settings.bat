@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYINSTALLER_RESET_ENVIRONMENT=1"
set "_MEIPASS2="

if exist "%APP_DIR%Bizant.exe" (
    "%APP_DIR%Bizant.exe" --settings
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
    "%APP_DIR%.venv\Scripts\python.exe" "%APP_DIR%config_gui.py"
) else (
    echo Python virtual environment not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
