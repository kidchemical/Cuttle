@echo off
setlocal EnableExtensions
title Cuttle
color 0B

rem ===== Paths (repo root = this script's ../..) =====
pushd "%~dp0..\.."
set "CUTTLE_ROOT=%CD%"
popd
set "VENV_PY=%CUTTLE_ROOT%\.venv\Scripts\python.exe"
set "DAEMON=%CUTTLE_ROOT%\src\scripts\cuttle_daemon.py"
set "ELECTRON_DIR=%CUTTLE_ROOT%\electron"
set "CUTTLE_EXE=%ELECTRON_DIR%\dist\win-unpacked\Cuttle.exe"
set "ICON=%CUTTLE_ROOT%\src\img\cuttle_logo.ico"
if defined CUTTLE_HOST (
  set "DEFAULT_HOST=%CUTTLE_HOST%"
) else (
  set "DEFAULT_HOST=127.0.0.1"
)

if not exist "%CUTTLE_ROOT%\.git" (
  echo ERROR: Cuttle checkout not found at:
  echo   %CUTTLE_ROOT%
  pause
  exit /b 1
)

rem Auto-install Desktop .lnk once (icon + stable target in the repo)
set "LNK=%USERPROFILE%\Desktop\Cuttle.lnk"
set "LAUNCHER=%CUTTLE_ROOT%\.cuttle\scripts\start-cuttle.cmd"
if not exist "%LAUNCHER%" copy /Y "%~f0" "%LAUNCHER%" >nul 2>&1
if not exist "%LNK%" if exist "%LAUNCHER%" call :MAKE_SHORTCUT_SILENT

rem Optional: first arg can skip the menu
if /I "%~1"=="client" goto START_CLIENT
if /I "%~1"=="host" goto START_HOST
if /I "%~1"=="shortcut" goto MAKE_SHORTCUT

:MENU
cls
echo.
echo   Cuttle launcher
echo   ---------------
echo   Checkout: %CUTTLE_ROOT%
echo.
echo   [1] Client  - Electron only, connect to Host ^(%DEFAULT_HOST%^)
echo   [2] Host    - Start daemon + app on this PC
echo   [3] Install Desktop shortcut ^(.lnk^)
echo   [Q] Quit
echo.
choice /C 123Q /N /M "Select"
if errorlevel 4 goto :eof
if errorlevel 3 goto MAKE_SHORTCUT
if errorlevel 2 goto START_HOST
if errorlevel 1 goto START_CLIENT
goto MENU

:START_CLIENT
echo.
echo Starting Cuttle Client -^> %DEFAULT_HOST% ...
if exist "%CUTTLE_EXE%" (
  start "Cuttle Client" /D "%ELECTRON_DIR%\dist\win-unpacked" "%CUTTLE_EXE%" --host=%DEFAULT_HOST%
  goto :eof
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo ERROR: No Cuttle.exe and npm.cmd not found.
  pause
  exit /b 1
)
pushd "%ELECTRON_DIR%"
start "Cuttle Client" npm.cmd start -- --host=%DEFAULT_HOST%
popd
goto :eof

:START_HOST
echo.
if not exist "%VENV_PY%" (
  echo ERROR: missing venv python:
  echo   %VENV_PY%
  pause
  exit /b 1
)
if not exist "%DAEMON%" (
  echo ERROR: missing daemon script:
  echo   %DAEMON%
  pause
  exit /b 1
)
echo Starting Cuttle Host ^(daemon + app^) ...
start "Cuttle Host" "%VENV_PY%" "%DAEMON%"
goto :eof

:MAKE_SHORTCUT
echo.
set "LAUNCHER=%CUTTLE_ROOT%\.cuttle\scripts\start-cuttle.cmd"
if not exist "%LAUNCHER%" set "LAUNCHER=%~f0"
set "LNK=%USERPROFILE%\Desktop\Cuttle.lnk"
call :MAKE_SHORTCUT_SILENT
if errorlevel 1 (
  echo Failed to create shortcut.
  pause
  exit /b 1
)
echo.
echo Desktop shortcut ready: Cuttle.lnk
echo ^(points at %LAUNCHER%^)
echo You can delete older Start-Cuttle-Client.cmd / Cuttle.cmd if you want.
echo.
pause
goto :eof

:MAKE_SHORTCUT_SILENT
set "LAUNCHER=%CUTTLE_ROOT%\.cuttle\scripts\start-cuttle.cmd"
if not exist "%LAUNCHER%" set "LAUNCHER=%~f0"
set "LNK=%USERPROFILE%\Desktop\Cuttle.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($env:LNK); $s.TargetPath = $env:LAUNCHER; $s.WorkingDirectory = (Split-Path $env:LAUNCHER); $s.WindowStyle = 1; $s.Description = 'Start Cuttle (Client or Host)'; if ($env:ICON -and (Test-Path $env:ICON)) { $s.IconLocation = ($env:ICON + ',0') }; $s.Save(); Write-Output ('Created ' + $env:LNK)"
exit /b %ERRORLEVEL%
