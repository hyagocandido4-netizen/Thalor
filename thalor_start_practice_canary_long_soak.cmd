@echo off
setlocal
if exist .\.venv\Scripts\python.exe (
  .\.venv\Scripts\python.exe "%~dp0thalor_start_practice_canary_long_soak.py" --repo-root "%CD%" --config config\practice_portfolio_canary.yaml %*
) else (
  py -3 "%~dp0thalor_start_practice_canary_long_soak.py" --repo-root "%CD%" --config config\practice_portfolio_canary.yaml %*
)
endlocal
