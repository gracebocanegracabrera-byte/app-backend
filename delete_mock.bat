@echo off
cd /d "%~dp0"
call venv\Scripts\activate
python scripts\delete_mock_properties.py
pause
