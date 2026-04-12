@echo off
setlocal
set SCRIPT_DIR=%~dp0
if exist "%SCRIPT_DIR%..\..\.venv\Scripts\python.exe" (
  "%SCRIPT_DIR%..\..\.venv\Scripts\python.exe" "%SCRIPT_DIR%capture_provider_stability_bundle.py" %*
  exit /b %errorlevel%
)
python "%SCRIPT_DIR%capture_provider_stability_bundle.py" %*
