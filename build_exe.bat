@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install pyinstaller --quiet
python -X utf8 scripts\build_exe.py
if errorlevel 1 (
  echo.
  echo Build failed. See the output above.
  pause
  exit /b 1
)
echo.
echo Done. See dist folder.
pause
