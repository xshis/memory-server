@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File install.ps1
echo.
pause
