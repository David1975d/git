@echo off
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Missing Python environment. See backend\README.md.
  pause
  exit /b 1
)
"backend\.venv\Scripts\python.exe" start.py
if errorlevel 1 pause
