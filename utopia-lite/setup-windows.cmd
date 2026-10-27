@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows.ps1" -Setup %*
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%