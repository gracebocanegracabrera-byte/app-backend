@echo off
echo Scrapeando propiedades reales de InfoCasas.com.pe (Trujillo)...
echo (puede tardar 30-60 segundos)
cd /d "%~dp0"
call venv\Scripts\activate.bat
python scripts/seed_properties.py
echo.
pause
