@echo off
title Cuttle LAN Firewall Setup
cd /d "E:\Dev\Cuttle"

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator privileges — approve the UAC prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory 'E:\Dev\Cuttle'"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "E:\Dev\Cuttle\src\scripts\enable_lan_firewall.ps1"
echo.
echo Window stays open so you can read output. Close when done.
pause
