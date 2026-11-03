@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if defined HOST_PYTHON (
  "%HOST_PYTHON%" -B BuildNative.py %*
) else (
  python -B BuildNative.py %*
)
exit /b %errorlevel%
