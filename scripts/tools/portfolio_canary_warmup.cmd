@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHONUTF8=1"
if exist "%SCRIPT_DIR%..\..\.venv\Scripts\python.exe" (
  "%SCRIPT_DIR%..\..\.venv\Scripts\python.exe" "%SCRIPT_DIR%portfolio_canary_warmup.py" %*
  exit /b %ERRORLEVEL%
)
python "%SCRIPT_DIR%portfolio_canary_warmup.py" %*
exit /b %ERRORLEVEL%
