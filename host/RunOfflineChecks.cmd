@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if defined HOST_PYTHON (
  "%HOST_PYTHON%" -B VerifyPackage.py --tests
) else (
  python -B VerifyPackage.py --tests
)
if errorlevel 1 pause
endlocal
