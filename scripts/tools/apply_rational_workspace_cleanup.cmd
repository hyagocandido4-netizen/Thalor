@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "PYTHONPATH=%REPO_ROOT%;%REPO_ROOT%\src"

if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
  "%REPO_ROOT%\.venv\Scripts\python.exe" "%SCRIPT_DIR%apply_rational_workspace_cleanup.py" --repo-root "%REPO_ROOT%" %*
  set "EXITCODE=%ERRORLEVEL%"
  endlocal & exit /b %EXITCODE%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%SCRIPT_DIR%apply_rational_workspace_cleanup.py" --repo-root "%REPO_ROOT%" %*
  set "EXITCODE=%ERRORLEVEL%"
  endlocal & exit /b %EXITCODE%
)

python "%SCRIPT_DIR%apply_rational_workspace_cleanup.py" --repo-root "%REPO_ROOT%" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
