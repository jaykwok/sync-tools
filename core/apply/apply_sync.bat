@echo off
chcp 65001 >nul
setlocal
set "APPLY_DIR=%~dp0"
set "APPLY_DIR=%APPLY_DIR:~0,-1%"
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    echo Please create the project .venv and install dependencies first.
    pause
    exit /b 1
)
cd /d "%ROOT%"
"%PYTHON%" "%APPLY_DIR%\apply_sync.py" "%ROOT%"
pause
endlocal
