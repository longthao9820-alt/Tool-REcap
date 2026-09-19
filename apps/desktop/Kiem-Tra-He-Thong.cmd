@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -m recap_tool.self_check
echo.
pause
