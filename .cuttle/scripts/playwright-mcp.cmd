@echo off
REM Portable Playwright MCP launcher — browser profile lives under LocalAppData,
REM not the Cuttle repo root.
setlocal
if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "CUTTLE_PW_DIR=%LOCALAPPDATA%\cuttle\runtime\playwright\mcp-chrome"
if not exist "%CUTTLE_PW_DIR%" mkdir "%CUTTLE_PW_DIR%"
npx -y @playwright/mcp@latest --browser chrome --caps vision,devtools --user-data-dir "%CUTTLE_PW_DIR%" %*
