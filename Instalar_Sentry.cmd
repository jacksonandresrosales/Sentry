@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   Instalador de Sentry para Windows
echo ========================================
echo.

where py >nul 2>nul
if not errorlevel 1 goto usar_py

where python >nul 2>nul
if not errorlevel 1 goto usar_python

echo ERROR: No se encontro Python 3.10 o superior.
echo Instalalo desde https://www.python.org/downloads/windows/
echo y activa la opcion "Add Python to PATH".
pause
exit /b 1

:usar_py
py -3 -c "import sys; raise SystemExit(0 if sys.version_info ^>= (3, 10) else 1)"
if errorlevel 1 goto version_incompatible
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
goto instalar

:usar_python
python -c "import sys; raise SystemExit(0 if sys.version_info ^>= (3, 10) else 1)"
if errorlevel 1 goto version_incompatible
if not exist ".venv\Scripts\python.exe" python -m venv .venv
goto instalar

:version_incompatible
echo ERROR: Sentry requiere Python 3.10 o superior.
pause
exit /b 1

:instalar
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: No se pudo crear el entorno virtual.
    pause
    exit /b 1
)

echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error_instalacion
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error_instalacion

echo Preparando la base de datos local...
".venv\Scripts\python.exe" -m app.database
if errorlevel 1 goto error_instalacion

echo.
echo Sentry quedo instalado correctamente.
if exist "%ProgramFiles(x86)%\WinSCP\WinSCPnet.dll" goto winscp_ok
if exist "%ProgramFiles%\WinSCP\WinSCPnet.dll" goto winscp_ok
if exist "%LOCALAPPDATA%\Programs\WinSCP\WinSCPnet.dll" goto winscp_ok
echo.
echo AVISO: Para conectarte a Issabel instala WinSCP con su componente .NET.
echo Las carpetas locales y NAS funcionan sin WinSCP.
goto finalizar

:winscp_ok
echo WinSCP detectado: la conexion con Issabel esta disponible.

:finalizar
echo.
choice /C SN /N /M "Deseas abrir Sentry ahora? [S/N]: "
if errorlevel 2 exit /b 0
call Sentry.cmd
exit /b 0

:error_instalacion
echo.
echo ERROR: La instalacion no pudo completarse. Revisa la conexion a Internet
echo y vuelve a ejecutar Instalar_Sentry.cmd.
pause
exit /b 1
