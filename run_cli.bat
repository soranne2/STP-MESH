@echo off
REM ============================================================
REM  STEP Mesher - command line mode
REM
REM  Usage:
REM    run_cli.bat cover.stp bracket.stp
REM    run_cli.bat *.stp --size 5 --hole-nodes 8 --washer 3
REM
REM  You can also drag STEP files onto this file in Explorer.
REM  Results are written to the "mesh_out" folder next to this file.
REM ============================================================
setlocal
cd /d "%~dp0"

set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo Environment not ready yet. Run run.bat once first.
    echo.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo No STEP file given.
    echo.
    echo   run_cli.bat cover.stp
    echo   run_cli.bat *.stp --size 5 --hole-nodes 8 --washer 3
    echo.
    echo Or drag STEP files onto this file.
    echo.
    pause
    exit /b 1
)

"%VPY%" cli.py %* -o mesh_out
echo.
pause
