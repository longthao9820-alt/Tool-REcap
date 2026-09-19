@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Đang đóng gói Toolrecap Auto thành file EXE mới...
python -m PyInstaller --noconfirm --clean Toolrecap-Auto.spec --distpath release --workpath build\pyinstaller-auto
if errorlevel 1 (
  echo.
  echo Đóng gói thất bại. Xem thông báo phía trên.
  pause
  exit /b 1
)
copy /y "%~dp0release\Toolrecap-Auto.exe" "%~dp0Toolrecap-Auto.exe" >nul
echo.
echo Đã tạo: %~dp0Toolrecap-Auto.exe
echo Bản RecapStudio.exe cũ không bị thay đổi.
pause

