@echo off
cd /d C:\TestingScreener
set PYTHONIOENCODING=utf-8
echo Starting Testing Screener...
echo Open http://localhost:8000 in your browser
echo.
C:\TestingScreener\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
