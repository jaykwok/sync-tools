@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "RUNNER=%~dp0run_build.py"
if not exist "%PYTHON%" (
    echo [ERROR] .venv\Scripts\python.exe not found: %PYTHON%
    pause
    exit /b 1
)
"%PYTHON%" "%RUNNER%" %1
pause
endlocal
