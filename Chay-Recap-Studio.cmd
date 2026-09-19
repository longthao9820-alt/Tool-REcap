@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0release\RecapStudio.exe" (
  start "" "%~dp0release\RecapStudio.exe"
  exit /b 0
)
if exist "%~dp0RecapStudio.exe" (
  start "" "%~dp0RecapStudio.exe"
  exit /b 0
)
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Không tìm thấy RecapStudio.exe hoặc Python 3.11 trở lên.
  echo Hãy chạy Dong-Goi-Recap-Studio.cmd để tạo bản EXE.
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0apps\desktop\main.py"
exit /b 0
