@echo off
REM Usage: run.bat "https://youtube.com/playlist?list=..." [limit]
if "%~1"=="" (
  echo Drag-drop? Opening UI...
  venv\Scripts\python.exe main.py
  exit /b
)
if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe main.py --url "%~1" --limit %~2
) else (
  python main.py --url "%~1" --limit %~2
)
pause
