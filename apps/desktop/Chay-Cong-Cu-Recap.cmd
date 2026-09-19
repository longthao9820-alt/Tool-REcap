@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Khong tim thay Python. Hay cai Python 3.11 tro len.
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0main.py"
exit /b 0

