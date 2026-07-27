@echo off
REM ============================================================
REM Rename folder: roadvision_script -> GroundTruth_script
REM Run this script AFTER closing VS Code completely.
REM ============================================================
echo Renaming project folder...
echo.

REM Try renaming from the Desktop
cd /d "%USERPROFILE%\Desktop"

if exist "roadvision_script" (
    ren "roadvision_script" "GroundTruth_script"
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not rename the folder.
        echo Make sure VS Code and any terminals using this folder are CLOSED.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo [SUCCESS] Folder renamed to: GroundTruth_script
    echo You can now open "C:\Users\%USERNAME%\Desktop\GroundTruth_script" in VS Code.
) else if exist "GroundTruth_script" (
    echo Folder is already named "GroundTruth_script". Nothing to do.
) else (
    echo [ERROR] Neither "roadvision_script" nor "GroundTruth_script" was found on the Desktop.
)

echo.
pause