@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\.."
if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
  "%REPO_ROOT%\.venv\Scripts\python.exe" "%SCRIPT_DIR%invoke_runtime_app.py" %*
  exit /b %ERRORLEVEL%
)
py -3.12 "%SCRIPT_DIR%invoke_runtime_app.py" %*
exit /b %ERRORLEVEL%
