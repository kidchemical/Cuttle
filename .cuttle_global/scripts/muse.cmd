@echo off
REM Forward to Meta's native Windows Muse install (not WSL).
REM Official install: %%LOCALAPPDATA%%\Programs\muse\muse.cmd → muse-bin-*.exe
setlocal EnableExtensions
set "MUSE_HOME=%LOCALAPPDATA%\Programs\muse"
if exist "%MUSE_HOME%\muse.cmd" (
  call "%MUSE_HOME%\muse.cmd" %*
  exit /b %ERRORLEVEL%
)
REM Fallback: newest muse-bin-*.exe in the install dir
for /f "delims=" %%F in ('dir /b /o-d "%MUSE_HOME%\muse-bin-*.exe" 2^>nul') do (
  "%MUSE_HOME%\%%F" %*
  exit /b %ERRORLEVEL%
)
echo muse: native Windows Muse Code not found. Install with: >&2
echo   irm https://dev.meta.ai/install.ps1 ^| iex >&2
exit /b 1
