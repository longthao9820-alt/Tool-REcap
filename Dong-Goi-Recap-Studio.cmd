@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Đang đóng gói Recap Studio thành file EXE...
python -m PyInstaller --noconfirm --clean RecapStudio.spec --distpath release --workpath build\pyinstaller
if errorlevel 1 (
  echo.
  echo Đóng gói thất bại. Xem thông báo phía trên.
  pause
  exit /b 1
)
copy /y "%~dp0release\RecapStudio.exe" "%~dp0RecapStudio.exe" >nul
echo.
echo Đã tạo: %~dp0RecapStudio.exe
pause
