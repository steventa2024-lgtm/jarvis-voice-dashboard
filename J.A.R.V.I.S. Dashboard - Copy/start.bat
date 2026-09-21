@echo off
rem ===========================================================================
rem  J.A.R.V.I.S. launcher
rem
rem  The dashboard must be reached as http://localhost, not as a file:// path
rem  and not over a plain http LAN address. Both the microphone and the API
rem  call require a secure context, and only https or localhost qualifies.
rem  This script exists so that is never got wrong.
rem ===========================================================================

cd /d "%~dp0"
set PORT=8123

where python >nul 2>nul
if errorlevel 1 goto nopython

python serve.py %PORT%
goto :eof

:nopython
echo.
echo   Python was not found on your PATH.
echo.
echo   Install it from python.org - tick "Add python.exe to PATH" during
echo   setup - then run this file again.
echo.
echo   Any static web server will do instead. The only hard requirement is
echo   that you open the dashboard as http://localhost:%PORT%
echo.
pause
