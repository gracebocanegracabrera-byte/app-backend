@echo off
cd /d "%~dp0"
call venv\Scripts\activate
python scripts\delete_kpi_snapshots.py
pause
