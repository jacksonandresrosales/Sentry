@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\build_exe.py
) else (
    py -3 scripts\build_exe.py
)

if errorlevel 1 (
    echo.
    echo No se pudo construir Sentry.exe. Instala primero requirements-build.txt.
    pause
    exit /b 1
)

echo.
echo El instalador Sentry_Setup con la version actual fue creado dentro de dist.
pause
