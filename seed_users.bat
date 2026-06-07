@echo off
echo Creando usuarios de prueba...
cd /d "%~dp0"
call venv\Scripts\activate.bat
python scripts/seed_users.py
echo.
pause
