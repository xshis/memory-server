@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo 还没有安装。先运行 install.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m kb scan %*
pause
