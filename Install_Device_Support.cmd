@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
echo Installing optional iPhone support into the local .venv folder.
echo Preview mode does not require this installation.
py -3.12 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-device.txt
if errorlevel 1 goto failed
echo.
echo Done. Run Launch.cmd to open YS-pikmin.
pause
exit /b 0
:failed
echo.
echo Installation failed. Check Python 3.12 and your Internet connection.
echo No security settings have been changed.
pause
exit /b 1
