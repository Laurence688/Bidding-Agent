@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1" -DownloadEmbedding
if errorlevel 1 (
  echo Installation failed. Check Python 3.11, Node.js 22.12+ and your network, then retry.
  pause
  exit /b 1
)
echo Installation complete. Double-click Laura.exe to start.
pause
