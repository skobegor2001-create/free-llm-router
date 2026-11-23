@echo off
rem ============================================================
rem  Free LLM Router - installer for Windows 11
rem  Double-click this file. No administrator rights needed.
rem  (Messages are ASCII on purpose: works on any console codepage.)
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Free LLM Router - Setup
chcp 65001 >nul

echo.
echo   ==========================================
echo     Free LLM Router - installer
echo     free neural networks for your agent
echo   ==========================================
echo.

rem ---------- 1. locate / copy files ----------
if exist "%~dp0app\server.py" goto :have_files

echo   [1/5] Copying files to %%LOCALAPPDATA%%\FreeLLMRouter ...
set "DEST=%LOCALAPPDATA%\FreeLLMRouter"
if not exist "%DEST%" mkdir "%DEST%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Copy-Item -Path (Join-Path '%~dp0' '*') -Destination '%DEST%' -Recurse -Force; exit 0"
if errorlevel 1 goto :copy_fail
cd /d "%DEST%"

:have_files
for %%I in ("%CD%.") do set "ROOT=%%~fI"
if not exist "%ROOT%\app\server.py" goto :copy_fail
if exist "%ROOT%\app\server.py" (set "APPDIR=%ROOT%\app") else (set "APPDIR=%ROOT%")
echo       OK: %ROOT%
echo.

rem ---------- 2. python ----------
echo   [2/5] Checking Python ...
set "PY=%ROOT%\runtime\python.exe"
if exist "%PY%" goto :py_found

set "PY="
call :resolve_python
call :py_version_ok
if defined PY goto :py_found

:py_install
where winget >nul 2>&1
if errorlevel 1 goto :no_winget
echo       Python 3.9+ not found - installing Python 3.12 via winget ...
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements --silent >nul 2>&1
call :refresh_path
set "PY="
call :resolve_python
call :py_version_ok
if defined PY goto :py_found
goto :no_winget

:py_found
"%PY%" --version
echo       OK.
echo.

rem ---------- 3. python packages ----------
echo   [3/5] Preparing Python packages ...
rem  portable: packages are already bundled -> nothing to do, works offline
"%PY%" -c "import fastapi, uvicorn, httpx, dotenv" >nul 2>&1
if not errorlevel 1 goto :deps_ok
echo       Installing packages (one time, 30-60 s) ...
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 call :install_pip
"%PY%" -m pip install --disable-pip-version-check -q -r "%APPDIR%\requirements.txt"
if errorlevel 1 goto :pip_fail
"%PY%" -c "import fastapi, uvicorn, httpx, dotenv" >nul 2>&1
if errorlevel 1 goto :pip_fail
:deps_ok
echo       OK.
echo.

rem ---------- 4. config ----------
echo   [4/5] Preparing config ...
if not exist "%APPDIR%\.env" copy /y "%APPDIR%\.env.example" "%APPDIR%\.env" >nul
echo       OK.
echo.

rem ---------- 5. shortcuts ----------
echo   [5/5] Creating shortcuts (Start menu + Desktop) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\windows\setup_shortcuts.ps1" -Root "%ROOT%"
if errorlevel 1 echo       (shortcuts were not created - not critical)
echo.

rem ---------- optional: keys ----------
echo   Free API keys (no credit card needed):
echo     1 console.groq.com/keys           GROQ_API_KEY
echo     2 cloud.cerebras.ai               CEREBRAS_API_KEY
echo     3 aistudio.google.com/apikey      GEMINI_API_KEY
echo     4 openrouter.ai/keys              OPENROUTER_API_KEY
echo     5 console.mistral.ai/api-keys     MISTRAL_API_KEY
echo.
set "ANS="
set /p "ANS=  Enter API key(s) now? [y/N]: "
if /i "%ANS%"=="y" "%PY%" "%ROOT%\windows\runtime\launcher.py" --keys
echo.

echo   Done! Starting Free LLM Router ...
"%PY%" "%ROOT%\windows\runtime\launcher.py" --open
echo.
echo   This window can be closed. Next time use the
echo   "Free LLM Router" shortcut in the Start menu.
echo.
pause
exit /b 0

rem ============================================================
rem  helpers
rem ============================================================

:resolve_python
where python >nul 2>&1
if errorlevel 1 goto :resolve_scan
for /f "delims=" %%P in ('where python') do if not defined PY set "PY=%%P"
if defined PY goto :eof
:resolve_scan
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
goto :eof

:py_version_ok
if not defined PY goto :eof
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if errorlevel 1 set "PY="
goto :eof

:refresh_path
for /f "skip=2 tokens=2,*" %%a in ('reg query HKCU\Environment /v Path 2^>nul') do set "UPATH=%%b"
for /f "skip=2 tokens=2,*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SPATH=%%b"
if not defined UPATH set "UPATH="
if not defined SPATH set "SPATH="
set "PATH=%UPATH%;%SPATH%;%PATH%"
goto :eof

:install_pip
echo       pip is missing - bootstrapping it ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing 'https://bootstrap.pypa.io/get-pip.py' -OutFile ($env:TEMP + '\get-pip.py') } catch { exit 1 }"
if errorlevel 1 goto :pip_fail
"%PY%" "%TEMP%\get-pip.py" --no-warn-script-location
goto :eof

rem ============================================================
rem  errors
rem ============================================================

:copy_fail
echo.
echo   ERROR: could not copy the files.
echo   Try unpacking the ZIP into a folder like C:\Users\%USERNAME%\FreeLLMRouter
echo   and run install.cmd again.
goto :fail

:pip_fail
echo.
echo   ERROR: could not install Python packages.
echo   Check the internet connection and run install.cmd again.
goto :fail

:no_winget
echo.
echo   ERROR: Python 3.9+ was not found and winget is unavailable.
echo   Install Python once from https://www.python.org/downloads/
echo   (tick "Add python.exe to PATH"), then run install.cmd again.
echo   Or use the installer package that already includes Python.
goto :fail

:fail
echo.
pause
exit /b 1
