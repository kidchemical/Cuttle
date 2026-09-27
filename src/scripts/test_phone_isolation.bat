@echo off
title Cuttle Phone Isolation Test
cd /d "E:\Dev\Cuttle"
echo Requesting Administrator (needed for test listener)...
powershell -NoProfile -Command "Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','E:\Dev\Cuttle\src\scripts\test_phone_isolation.ps1' -Verb RunAs"
pause
