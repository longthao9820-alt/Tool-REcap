@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0release\BodycamStudio.exe" (
  start "" "%~dp0release\BodycamStudio.exe"
  exit /b 0
)
if exist "%~dp0BodycamStudio.exe" (
  start "" "%~dp0BodycamStudio.exe"
  exit /b 0
)
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Không tìm thấy BodycamStudio.exe hoặc Python 3.11 trở lên.
  echo Hãy chạy Dong-Goi-Bodycam-Studio.cmd để tạo bản EXE.
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0apps\desktop\bodycam_main.py"
exit /b 0
