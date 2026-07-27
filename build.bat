@echo off
REM ============================================================
REM build.bat - Build GroundTruth.exe (onefile, windowed)
REM ============================================================
REM Usage:
REM   build.bat        - build
REM   build.bat clean  - delete build/ dist/ then build
REM ============================================================
setlocal enableextensions

cd /d "%~dp0"

set "PY=.venv310\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Kill any running instance so the exe isn't locked during rebuild
taskkill /F /IM GroundTruth.exe >nul 2>&1

if /i "%~1"=="clean" (
    echo Cleaning previous build...
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
) else (
    REM Ensure dist exe isn't locked even on incremental builds
    if exist dist\GroundTruth.exe del /F /Q dist\GroundTruth.exe 2>nul
)

echo Using Python: %PY%
echo.

echo Checking PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found, installing...
    "%PY%" -m pip install pyinstaller || goto :fail
)

echo Building GroundTruth.exe...
"%PY%" -m PyInstaller groundtruth.spec --noconfirm || goto :fail

if exist "dist\GroundTruth.exe" (
    echo.
    echo ============================================
    echo  BUILD SUCCESS
    echo  Output: %CD%\dist\GroundTruth.exe
    echo  Just copy that .exe and share it like an .apk
    echo ============================================
    endlocal & exit /b 0
)

:fail
echo.
echo BUILD FAILED - check output above.
endlocal & exit /b 1