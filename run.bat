@echo off
REM ============================================================
REM  STEP Mesher launcher
REM  - first run  : creates .venv and installs gmsh / PySide6
REM  - later runs : starts the GUI right away
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- corporate proxy: uncomment and fill in if pip cannot reach the internet
REM set HTTP_PROXY=http://proxy.example.com:8080
REM set HTTPS_PROXY=http://proxy.example.com:8080

set "VENV=.venv"
set "PY="

where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/3] Creating virtual environment in %VENV% ...
    %PY% -m venv "%VENV%"
    if errorlevel 1 goto :venv_failed
) else (
    echo [1/3] Virtual environment found.
)

set "VPY=%VENV%\Scripts\python.exe"

"%VPY%" -c "import gmsh, PySide6" >nul 2>nul
if errorlevel 1 (
    echo [2/3] Installing gmsh and PySide6 . This can take a few minutes.
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 goto :pip_failed
) else (
    echo [2/3] Packages already installed.
)

echo [3/3] Starting STEP Mesher ...
echo.
"%VPY%" app.py
if errorlevel 1 goto :app_failed
exit /b 0


:no_python
echo.
echo Python was not found on this PC.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and be sure to tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:venv_failed
echo.
echo Failed to create the virtual environment.
echo Try deleting the .venv folder and running this file again.
echo.
pause
exit /b 1

:pip_failed
echo.
echo Package installation failed.
echo If this PC sits behind a proxy, open run.bat in a text editor and
echo fill in the HTTP_PROXY / HTTPS_PROXY lines near the top.
echo.
pause
exit /b 1

:app_failed
echo.
echo STEP Mesher exited with an error. The message above has the details.
echo.
pause
exit /b 1
