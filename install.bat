@echo off
cd /d C:\
echo ============================================
echo   Spider XHS - First Time Setup
echo ============================================
echo.

echo [1/3] Cloning from GitHub...
git clone https://github.com/shatp984-rgb/XHS.git xhs
cd xhs
echo OK
echo.

echo [2/3] Creating virtual environment...
python -m venv .venv
call .venv\Scripts\activate.bat
echo OK
echo.

echo [3/3] Installing dependencies (may take a few minutes)...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Installation failed. Send error above to developer.
    pause
    exit /b 1
)
echo OK
echo.

echo ============================================
echo   Setup complete!
echo   Double-click start.bat to run the system.
echo   Browser: http://localhost:8000
echo ============================================
pause
