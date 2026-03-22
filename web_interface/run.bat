@echo off
cd /d "%~dp0"
echo Cai dat thu vien...
pip install -r requirements.txt
echo.
echo Khoi dong server...
echo Truy cap: http://localhost:5000
python server.py
pause
