@echo off
chcp 65001 >nul
setlocal
set "APPLY_DIR=%~dp0"
set "APPLY_DIR=%APPLY_DIR:~0,-1%"
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
cd /d "%ROOT%"
"%PYTHON%" "%APPLY_DIR%\apply_sync.py" "%ROOT%"
pause
endlocal
