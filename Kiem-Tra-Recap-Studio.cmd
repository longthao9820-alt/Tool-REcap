@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0apps\desktop;%~dp0backend"
python -m recap_tool.self_check
if errorlevel 1 goto :done
python -m unittest discover -s apps\desktop\tests -v
:done
echo.
pause

