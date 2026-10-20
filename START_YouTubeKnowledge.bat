@echo off
setlocal
cd /d "%~dp0"
set "LAUNCH_PY_ARGS="
chcp 65001 >nul

if exist ".venv\Scripts\python.exe" goto localvenv
if exist "venv\Scripts\python.exe" goto legacyvenv
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto pythonlauncher
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto systempython

echo [ERROR] Install Python 3.10 or newer from https://www.python.org/downloads/
echo Enable "Add Python to PATH" or install the Python launcher.
if not defined KNOWLEDGE_NO_PAUSE pause
exit /b 1

:localvenv
set "LAUNCH_PY=.venv\Scripts\python.exe"
goto run
:legacyvenv
set "LAUNCH_PY=venv\Scripts\python.exe"
goto run
:pythonlauncher
set "LAUNCH_PY=py"
set "LAUNCH_PY_ARGS=-3"
goto run
:systempython
set "LAUNCH_PY=python"

:run
"%LAUNCH_PY%" %LAUNCH_PY_ARGS% helper_scripts\bootstrap.py %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not "%LAUNCH_EXIT%"=="0" (
    echo.
    echo [ERROR] Launch failed. Review the message above and logs in the project logs folder.
    if not defined KNOWLEDGE_NO_PAUSE pause
)
exit /b %LAUNCH_EXIT%
