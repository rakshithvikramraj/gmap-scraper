@echo off
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv...
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Installing Python...
uv python install 3.12

echo Installing dependencies...
uv sync

echo Downloading the browser (about 150MB, one time)...
uv run playwright install chromium

echo.
echo Setup complete. Double-click run.bat to start.
pause
