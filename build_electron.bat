@echo off
echo ========================================
echo Cuttle Desktop - Build Installer
echo ========================================
echo.

REM Check if Node.js is installed
where node >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Node.js is not installed!
    echo.
    echo Please install Node.js from: https://nodejs.org/
    echo.
    pause
    exit /b 1
)

REM Navigate to electron directory
cd /d "%~dp0electron"

REM Install dependencies if needed
if not exist "node_modules" (
    echo Installing dependencies...
    echo.
    call npm install
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo ERROR: Failed to install dependencies!
        echo.
        pause
        exit /b 1
    )
)

echo.
echo ========================================
echo Build Options:
echo ========================================
echo.
echo 1. Build Full Installer (NSIS)
echo 2. Build Portable Version
echo 3. Build Both
echo 4. Build Directory (for testing)
echo 5. Cancel
echo.
set /p choice="Enter your choice (1-5): "

if "%choice%"=="1" goto build_installer
if "%choice%"=="2" goto build_portable
if "%choice%"=="3" goto build_both
if "%choice%"=="4" goto build_dir
if "%choice%"=="5" goto end

echo Invalid choice!
pause
exit /b 1

:build_installer
echo.
echo Building installer...
echo This may take several minutes...
echo.
call npm run build -- --win nsis
goto check_result

:build_portable
echo.
echo Building portable version...
echo This may take several minutes...
echo.
call npm run build:portable
goto check_result

:build_both
echo.
echo Building both installer and portable version...
echo This may take several minutes...
echo.
call npm run build
goto check_result

:build_dir
echo.
echo Building unpacked directory...
echo.
call npm run build:dir
goto check_result

:check_result
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================
    echo Build FAILED!
    echo ========================================
    echo.
    echo Please check the error messages above.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Build completed successfully!
echo ========================================
echo.
echo Output location: %~dp0electron\dist\
echo.
echo Files created:
dir /b "%~dp0electron\dist\" 2>nul
echo.
echo You can now:
echo  1. Run the installer to install Cuttle
echo  2. Run the portable version directly
echo  3. Share the installer with others
echo.
echo ========================================
echo.

REM Ask if user wants to open the dist folder
set /p open="Open the dist folder? (Y/N): "
if /i "%open%"=="Y" (
    explorer "%~dp0electron\dist"
)

:end
echo.
pause

