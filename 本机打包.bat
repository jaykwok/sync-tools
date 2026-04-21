@echo off
chcp 65001 >nul
setlocal
set "SYNC=%~dp0"
for %%I in ("%SYNC%..") do set "ROOT=%%~fI"

set "VENV_PYTHON="
for /f "usebackq tokens=1,* delims==" %%A in ("%SYNC%.env") do (
    if "%%A"=="VENV_PYTHON" set "VENV_PYTHON=%%B"
)
if not defined VENV_PYTHON set "VENV_PYTHON=.venv\Scripts\python.exe"

set "PYTHON=%ROOT%\%VENV_PYTHON%"
set "RUNNER=%SYNC%core\build\run_build.py"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    echo Check VENV_PYTHON in sync-tools\.env
    pause
    exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" "%RUNNER%" %1
pause
endlocal
