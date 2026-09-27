@echo off
echo ========================================
echo Cuttle Desktop - Electron Launcher
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

REM Check if npm is installed
where npm >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: npm is not installed!
    echo.
    echo npm should come with Node.js. Please reinstall Node.js.
    echo.
    pause
    exit /b 1
)

echo ✓ Node.js detected: 
node --version
echo ✓ npm detected:
call npm --version
echo.

REM Navigate to electron directory
cd /d "%~dp0electron"

REM Check if node_modules exists
if not exist "node_modules" (
    echo Installing dependencies for the first time...
    echo This may take a few minutes...
    echo.
    call npm install
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo ERROR: Failed to install dependencies!
        echo.
        pause
        exit /b 1
    )
    echo.
    echo ✓ Dependencies installed successfully!
    echo.
)

REM Start the Electron app
echo Starting Cuttle Desktop Application...
set CUTTLE_DESKTOP_VER=
for /f "delims=" %%v in ('node -p "require('./package.json').version" 2^>nul') do set CUTTLE_DESKTOP_VER=%%v
if defined CUTTLE_DESKTOP_VER (
    echo Version: %CUTTLE_DESKTOP_VER%
) else (
    echo Version: see electron/package.json
)
echo.
echo If this is your first run:
echo  - The Python Flask server will start
echo  - A window will open with the Cuttle interface
echo  - Wait a few seconds for everything to load
echo.
echo To stop the application:
echo  - Close the Cuttle window
echo  - Or press Ctrl+C in this console
echo.
echo ========================================
echo.

call npm start

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================
    echo Application exited with an error!
    echo ========================================
    echo.
    echo Common issues:
    echo  1. Python not installed or not in PATH
    echo  2. Required Python packages not installed
    echo  3. Port 8080 already in use
    echo.
    echo Please check the error messages above.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Application closed successfully
echo ========================================
pause

