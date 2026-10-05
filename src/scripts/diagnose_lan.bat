@echo off
title Cuttle LAN Diagnostics
cd /d "%~dp0..\.."
echo.
echo === Cuttle LAN Diagnostics ===
echo.

for /f "tokens=*" %%i in ('call "%~dp0..\..\.venv\Scripts\python.exe" -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('10.255.255.255',1));print(s.getsockname()[0])" 2^>nul') do set LANIP=%%i
if "%LANIP%"=="" set LANIP=unknown

echo LAN IP: %LANIP%
echo.
echo Listening ports:
netstat -an | findstr ":8080 :8888" | findstr LISTENING
echo.
echo Firewall rules (needs admin to list sometimes):
netsh advfirewall firewall show rule name="Cuttle LAN HTTP (LocalSubnet)" 2>nul | findstr /i "Enabled Profiles LocalPort Action"
netsh advfirewall firewall show rule name="Cuttle LAN HTTPS (LocalSubnet)" 2>nul | findstr /i "Enabled Profiles LocalPort Action"
echo.
echo Wi-Fi profile:
powershell -NoProfile -Command "Get-NetConnectionProfile | Format-Table InterfaceAlias,NetworkCategory -AutoSize"
echo.
echo PC self-test (HTTPS phone port via LAN IP):
"%~dp0..\..\.venv\Scripts\python.exe" -c "import urllib.request,ssl; ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; r=urllib.request.urlopen('https://%LANIP%:8888/api/lan-ping', context=ctx, timeout=5); print(r.read().decode())" 2>nul
if errorlevel 1 echo   FAILED - restart Cuttle after code update
echo.
echo PC self-test (HTTP fallback port 8000):
"%~dp0..\..\.venv\Scripts\python.exe" -c "import urllib.request; r=urllib.request.urlopen('http://%LANIP%:8000/api/lan-ping', timeout=5); print(r.read().decode())" 2>nul
if errorlevel 1 echo   FAILED - port 8000 not listening yet
echo.
echo === On your phone (same Wi-Fi, NOT mobile data) ===
echo   https://%LANIP%:8888/api/lan-ping   (accept cert warning)
echo   http://%LANIP%:8000/api/lan-ping    (plain HTTP fallback)
echo   https://%LANIP%:8888/phone
echo.
echo On PC first (QR code): https://127.0.0.1:8080/phone
echo.
echo iPhone: Settings - Privacy - Local Network - enable Safari
echo If still failing: router may block phone-to-PC (AP isolation). Use main Wi-Fi, not guest.
echo.
pause
