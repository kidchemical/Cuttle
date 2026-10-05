@echo off
title Cuttle LAN Firewall Setup
cd /d "%~dp0..\.."

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator privileges — approve the UAC prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_lan_firewall.ps1"
echo.
echo Window stays open so you can read output. Close when done.
pause
