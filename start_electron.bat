@echo off
REM Explicit local Host; setup owns checks and partial-install repair.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" -LaunchOnly %*
exit /b %ERRORLEVEL%
