@echo off
rem Start Vein Assist (Windows). Double-click this file, or run it from a terminal.
rem The first run creates the virtual environment and installs the Python packages.
setlocal
cd /d "%~dp0"

if exist ".venv\installed.ok" goto :key
call :find_python || goto :fail
echo First run: installing Python packages, this can take a few minutes...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto :fail_setup
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail_setup
echo ok> ".venv\installed.ok"

:key
if not exist ".env" copy ".env.example" ".env" >nul
findstr /r /c:"^GEMINI_API_KEY=." ".env" >nul && goto :run
echo.
echo Your Gemini API key is missing. Notepad will open .env now:
echo paste the key after GEMINI_API_KEY= then save and close Notepad.
echo Get a key at https://aistudio.google.com/apikey
notepad ".env"
findstr /r /c:"^GEMINI_API_KEY=." ".env" >nul && goto :run
echo Still no key in .env, so the assistant can't answer. Add it and run this again.
goto :fail

:run
".venv\Scripts\python.exe" server.py
if errorlevel 1 pause
exit /b

:find_python
rem Needs Python 3.11+. Prefer the "py" launcher; plain "python" can be a Microsoft Store placeholder that doesn't run.
set "CHECK=import sys; sys.exit(sys.version_info < (3, 11))"
py -3 -c "%CHECK%" >nul 2>&1 && (set "PY=py -3" & exit /b 0)
python -c "%CHECK%" >nul 2>&1 && (set "PY=python" & exit /b 0)
echo Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup, then run this again.
exit /b 1

:fail_setup
echo Setup failed. Check the internet connection, then run this again.
if exist ".venv" rmdir /s /q ".venv"
:fail
pause
exit /b 1
