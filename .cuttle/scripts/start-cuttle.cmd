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
  set "DEFAULT_HOST=saved server"
)

if not exist "%CUTTLE_ROOT%\.git" (
  echo ERROR: Cuttle checkout not found at:
  echo   %CUTTLE_ROOT%
  pause
  exit /b 1
)

rem Auto-install the Desktop shortcuts once (Cuttle Host / Client / Daemon).
set "LAUNCHER=%CUTTLE_ROOT%\.cuttle\scripts\start-cuttle.cmd"
if not exist "%LAUNCHER%" copy /Y "%~f0" "%LAUNCHER%" >nul 2>&1
set "SHORTCUTS_PS1=%CUTTLE_ROOT%\src\scripts\create_desktop_shortcuts.ps1"
if not exist "%USERPROFILE%\Desktop\Cuttle Host.lnk" if exist "%SHORTCUTS_PS1%" powershell -NoProfile -ExecutionPolicy Bypass -File "%SHORTCUTS_PS1%" -Quiet >nul 2>&1

rem Optional: first arg can skip the menu
if /I "%~1"=="client" goto START_CLIENT
if /I "%~1"=="host" goto START_HOST
if /I "%~1"=="daemon" goto START_DAEMON
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
echo   [3] Daemon  - Start the daemon only ^(no app^)
echo   [4] Install Desktop shortcuts ^(Cuttle Host / Client / Daemon^)
echo   [Q] Quit
echo.
choice /C 1234Q /N /M "Select"
if errorlevel 5 goto :eof
if errorlevel 4 goto MAKE_SHORTCUT
if errorlevel 3 goto START_DAEMON
if errorlevel 2 goto START_HOST
if errorlevel 1 goto START_CLIENT
goto MENU

:START_CLIENT
echo.
echo Starting Cuttle Client -^> %DEFAULT_HOST% ...
if exist "%CUTTLE_EXE%" (
  if defined CUTTLE_HOST (
    start "Cuttle Client" /D "%ELECTRON_DIR%\dist\win-unpacked" "%CUTTLE_EXE%" --client --host="%CUTTLE_HOST%"
  ) else (
    start "Cuttle Client" /D "%ELECTRON_DIR%\dist\win-unpacked" "%CUTTLE_EXE%" --client
  )
  goto :eof
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo ERROR: No Cuttle.exe and npm.cmd not found.
  pause
  exit /b 1
)
pushd "%ELECTRON_DIR%"
if defined CUTTLE_HOST (
  start "Cuttle Client" npm.cmd start -- --client --host="%CUTTLE_HOST%"
) else (
  start "Cuttle Client" npm.cmd start -- --client
)
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

:START_DAEMON
echo.
if not exist "%VENV_PY%" (
  echo ERROR: missing venv python:
  echo   %VENV_PY%
  pause
  exit /b 1
)
echo Starting Cuttle daemon only ^(no app^) ...
start "Cuttle Daemon" "%VENV_PY%" "%DAEMON%"
goto :eof

:MAKE_SHORTCUT
echo.
set "SHORTCUTS_PS1=%CUTTLE_ROOT%\src\scripts\create_desktop_shortcuts.ps1"
if exist "%SHORTCUTS_PS1%" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SHORTCUTS_PS1%"
) else (
  echo Missing %SHORTCUTS_PS1%
)
echo.
pause
goto :eof
