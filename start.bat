@echo off
cd /d C:\XHS
call .venv\Scripts\activate.bat
python main.py --host 0.0.0.0 --port 8000
pause
