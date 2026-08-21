@echo off
rem Start XHS backend (8000) + frontend dev (5173) with NO console window
powershell -NoProfile -Command "Start-Process -FilePath 'C:\XHS\.venv\Scripts\python.exe' -ArgumentList 'main.py','--host','0.0.0.0','--port','8000','--with-frontend' -WorkingDirectory 'C:\XHS' -WindowStyle Hidden"
echo XHS services started hidden. Use stop.bat to stop them.
timeout /t 2 >nul