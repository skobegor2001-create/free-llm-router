@echo off
rem Free LLM Router - Windows launcher (menu). ASCII only: safe on any codepage.
title Free LLM Router
setlocal EnableExtensions
for %%I in ("%~dp0.") do set "ROOT=%%~fI"

rem --- find interpreter: portable runtime -> PATH -> standard install dirs ---
set "PY="
if exist "%ROOT%\runtime\python.exe" set "PY=%ROOT%\runtime\python.exe"
if exist "%ROOT%\python\python.exe" set "PY=%ROOT%\python\python.exe"

if not defined PY call :resolve_python
if not defined PY goto :no_python
call :py_version_ok
if not defined PY goto :no_python

set "FREELMR_PYTHON=%PY%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%PY%" "%ROOT%\windows\runtime\launcher.py" %*
if errorlevel 1 pause
exit /b 0

:resolve_python
where python >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%P in ('where python') do if not defined PY set "PY=%%P"
)
if defined PY goto :eof
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python31*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
if defined PY goto :eof
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python39*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
goto :eof

:py_version_ok
if not defined PY goto :eof
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if errorlevel 1 set "PY="
goto :eof

:no_python
echo.
echo   Python 3.9+ not found.
echo   Run the Free LLM Router installer again, or install Python from
echo   https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo.
pause
exit /b 1
