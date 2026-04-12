@echo off
setlocal
set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..\..
if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
  set PY_EXE=%REPO_ROOT%\.venv\Scripts\python.exe
) else (
  set PY_EXE=python
)
"%PY_EXE%" "%SCRIPT_DIR%canary_go_no_go.py" %*
exit /b %ERRORLEVEL%
