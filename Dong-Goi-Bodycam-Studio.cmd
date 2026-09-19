@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Đang đóng gói Bodycam Studio thành file EXE...
python -m PyInstaller --noconfirm --clean BodycamStudio.spec --distpath release --workpath build\pyinstaller-bodycam
if errorlevel 1 (
  echo.
  echo Đóng gói thất bại. Xem thông báo phía trên.
  pause
  exit /b 1
)
echo.
echo Đã tạo: %~dp0release\BodycamStudio.exe
pause
