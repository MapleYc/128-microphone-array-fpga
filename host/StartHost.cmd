@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if defined HOST_PYTHON (
  "%HOST_PYTHON%" -B StartHost.py %*
) else (
  python -B StartHost.py %*
)
if errorlevel 1 pause
endlocal
