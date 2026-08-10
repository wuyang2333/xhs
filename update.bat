@echo off
cd /d C:\XHS
echo Updating...
git fetch origin
git reset --hard origin/main
echo.
echo Installing new dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt
echo.
echo ============================================
echo   Update done. Press any key to restart.
echo ============================================
pause
python main.py --host 0.0.0.0 --port 8000
pause
