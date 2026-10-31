@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" %*
set "result=%ERRORLEVEL%"
echo.
if not "%result%"=="0" echo Setup failed. Logs and existing data have been preserved.
pause
exit /b %result%
