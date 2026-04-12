@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\.."
if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
  set "PY=%REPO_ROOT%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%"
"%PY%" "%SCRIPT_DIR%\portfolio_artifact_repair.py" %*
exit /b %ERRORLEVEL%
