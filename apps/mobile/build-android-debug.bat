@echo off
REM Build Cuttle Mobile debug APK (uses Android Studio JDK + local SDK).
setlocal
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Android\Android Studio\jbr"
if not exist "%JAVA_HOME%\bin\java.exe" (
  echo Android Studio JBR not found at %JAVA_HOME%
  echo Install Android Studio or set JAVA_HOME to JDK 21.
  exit /b 1
)
if not exist "android\local.properties" (
  if exist "%LOCALAPPDATA%\Android\Sdk" (
    echo sdk.dir=%LOCALAPPDATA:\=\\%\Android\Sdk> android\local.properties
    echo Created android\local.properties
  ) else (
    echo Copy android\local.properties.example to android\local.properties and set sdk.dir
    exit /b 1
  )
)
cd /d "%~dp0android"
call gradlew.bat assembleDebug %*
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
echo.
echo APK: android\app\build\outputs\apk\debug\app-debug.apk
endlocal
