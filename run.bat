@echo off
cd /d "%~dp0"
uv run python app.py
if errorlevel 1 pause
