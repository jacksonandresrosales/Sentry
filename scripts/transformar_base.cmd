@echo off
setlocal
cd /d "%~dp0.."
set "SENTRY_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "SENTRY_SCRIPT=%~dp0transformar_base.py"
if "%~1"=="" set "SENTRY_SCRIPT=%~dp0transformar_base_app.py"
if "%~1"=="" if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe" (
    start "" "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe" "%SENTRY_SCRIPT%"
    exit /b
)
if exist "%SENTRY_PYTHON%" (
    "%SENTRY_PYTHON%" "%SENTRY_SCRIPT%" %*
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%SENTRY_SCRIPT%" %*
    exit /b
)
python "%SENTRY_SCRIPT%" %*
exit /b
