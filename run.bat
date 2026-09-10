@echo off
REM ============================================================
REM  STEP Mesher launcher
REM  Opens a small setup window (bootstrap.py) that creates the
REM  virtual environment, installs gmsh / PySide6 and starts the GUI.
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- corporate proxy: uncomment and fill in if pip cannot reach the internet
REM set HTTP_PROXY=http://proxy.example.com:8080
REM set HTTPS_PROXY=http://proxy.example.com:8080

set "PY="
where pyw >nul 2>nul && set "PY=pyw -3"
if not defined PY where pythonw >nul 2>nul && set "PY=pythonw"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

start "" %PY% bootstrap.py
exit /b 0

:no_python
echo.
echo Python was not found on this PC.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and be sure to tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1
