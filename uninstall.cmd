@echo off
rem Free LLM Router - uninstaller for Windows 11
setlocal EnableExtensions
cd /d "%~dp0"
title Free LLM Router - Uninstall
chcp 65001 >nul

for %%I in ("%CD%.") do set "ROOT=%%~fI"

set "PY=%ROOT%\runtime\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   Stopping the server ...
"%PY%" "%ROOT%\windows\runtime\launcher.py" --stop >nul 2>&1

echo   Removing shortcuts ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path ([Environment]::GetFolderPath('Programs')) 'Free LLM Router'; if (Test-Path $p) { Remove-Item $p -Recurse -Force }; $d=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Free LLM Router.lnk'; if (Test-Path $d) { Remove-Item $d -Force }" >nul 2>&1

echo.
set "ANS="
set /p "ANS=  Delete program files and API keys (.env)? [y/N]: "
if /i not "%ANS%"=="y" goto :done

echo   Deleting %ROOT% ...
set "TARGET=%ROOT%"
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Remove-Item -LiteralPath $env:TARGET -Recurse -Force -ErrorAction SilentlyContinue"

:done
echo.
echo   Done. Good bye.
timeout /t 3 >nul
exit /b 0
