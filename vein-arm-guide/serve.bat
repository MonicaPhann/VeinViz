@echo off
rem Serve the arm page on port 8081 (Windows). Open http://localhost:8081 here,
rem or http://<this-computer's-IP>:8081 on the Quest.
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set "PORT=8081"
call :find_python || goto :fail
echo Open http://localhost:%PORT% in your browser.
%PY% -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); print('Other devices on this network and the Quest: http://' + s.getsockname()[0] + ':%PORT%')" 2>nul
%PY% -m http.server %PORT% --bind 0.0.0.0
exit /b

:find_python
rem Prefer the "py" launcher; plain "python" can be a Microsoft Store placeholder that doesn't run.
py -3 --version >nul 2>&1 && (set "PY=py -3" & exit /b 0)
python --version >nul 2>&1 && (set "PY=python" & exit /b 0)
echo Python 3 was not found. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup, then run this again.
exit /b 1

:fail
pause
exit /b 1
