@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m app.main
    if errorlevel 1 pause
    exit /b
)
set "SENTRY_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%SENTRY_PYTHON%" (
    "%SENTRY_PYTHON%" -m app.main
    if errorlevel 1 pause
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m app.main
    if errorlevel 1 pause
    exit /b
)
python -m app.main
if errorlevel 1 pause
