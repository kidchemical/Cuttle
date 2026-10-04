@echo off
 title Cuttle Phone Connectivity Diagnostic
 powershell -NoProfile -Command "Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0diagnose_phone_connectivity.ps1' -Verb RunAs"
 pause
