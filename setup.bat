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
if errorlevel 1 goto :fail

echo Installing dependencies...
uv sync
if errorlevel 1 goto :fail

echo Downloading the browser (about 150MB, one time)...
uv run playwright install chromium
if errorlevel 1 goto :fail

echo.
echo Setup complete. Double-click run.bat to start.
pause
exit /b 0

:fail
echo.
echo Setup FAILED at the step above. Nothing was installed incorrectly -- you can
echo re-run setup.bat once the problem is fixed.
pause
exit /b 1
