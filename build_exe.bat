@echo off
cd /d "%~dp0"
python -m pip install pyinstaller --quiet
python -m PyInstaller desktop_app.spec --noconfirm
if errorlevel 1 pause
