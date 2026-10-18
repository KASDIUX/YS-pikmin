@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" start.py
) else (
  py -3.12 start.py
)
if errorlevel 1 (
  echo.
  echo Could not start YS-pikmin. Python 3.12 is required.
  echo If port 8765 is busy, close the previous YS-pikmin window first.
  pause
)
