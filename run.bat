@echo off
cd /d "%~dp0"
set "YTK_RUN_PYTHON=python"
if exist venv\Scripts\python.exe set "YTK_RUN_PYTHON=venv\Scripts\python.exe"
set "YTK_RUN_LIMIT=0"
if not "%~2"=="" set "YTK_RUN_LIMIT=%~2"
REM Usage: run.bat "https://youtube.com/playlist?list=..." [limit]
if "%~1"=="" (
  echo Drag-drop? Opening UI...
  "%YTK_RUN_PYTHON%" main.py
  exit /b
)
"%YTK_RUN_PYTHON%" main.py --url "%~1" --limit "%YTK_RUN_LIMIT%"
pause
