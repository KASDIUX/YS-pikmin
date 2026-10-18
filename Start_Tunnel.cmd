@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
echo For iOS 17 or later, right-click this file and choose Run as administrator.
echo Keep this window open while connected. Press Ctrl+C to stop the tunnel.
echo This service listens only on 127.0.0.1.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pymobiledevice3 remote tunneld --host 127.0.0.1
) else (
  py -3.12 -m pymobiledevice3 remote tunneld --host 127.0.0.1
)
pause
