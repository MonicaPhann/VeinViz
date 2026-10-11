@echo off
rem Start Vein Assist (Windows). First run creates the virtualenv and installs dependencies.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run
call :find_python || goto :fail
echo First run: installing Python packages, this can take a few minutes...
%PY% -m venv .venv || goto :fail_setup
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail_setup

:run
if not exist ".env" copy ".env.example" ".env" >nul
".venv\Scripts\python.exe" server.py
if errorlevel 1 pause
exit /b

:find_python
rem Prefer the "py" launcher; plain "python" can be a Microsoft Store placeholder that doesn't run.
py -3 --version >nul 2>&1 && (set "PY=py -3" & exit /b 0)
python --version >nul 2>&1 && (set "PY=python" & exit /b 0)
echo Python 3 was not found. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup, then run this again.
exit /b 1

:fail_setup
echo Setup failed. Check the internet connection, then run this again.
if exist ".venv" rmdir /s /q ".venv"
:fail
pause
exit /b 1
