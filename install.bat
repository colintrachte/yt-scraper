@echo off
echo === yt-archive-seed installer ===
where python >nul 2>nul
if %errorlevel% neq 0 (
  echo Python not found! Install Python 3.10+ from python.org and CHECK "Add to PATH"
  pause
  exit /b
)

echo Creating venv...
python -m venv venv
echo Activating and installing...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Done! Now run: run.bat "https://www.youtube.com/playlist?list=..."
echo Or double-click main.py to open the UI
pause
