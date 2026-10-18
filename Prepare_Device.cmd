@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" prepare_device.py
) else (
  py -3.12 prepare_device.py
)
pause
