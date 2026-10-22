@echo off
cd /d "%~dp0"
python -X utf8 app\desktop_app.py
if errorlevel 1 pause
