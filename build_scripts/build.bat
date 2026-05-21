@echo off
REM ============================================================================
REM  PostgreSQL Backup and Restore - Windows Build Script (CMD)
REM  Builds a standalone .exe using PyInstaller and creates a desktop shortcut.
REM ============================================================================

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%~dp0.."
set "APP_NAME=PostgreSQL Backup and Restore"
set "EXE_NAME=PgBackupRestore"
set "ICON_PATH=%~dp0app_icon.ico"
set "ENTRY_POINT=%PROJECT_ROOT%\main.py"
set "DIST_DIR=%PROJECT_ROOT%\dist"
set "BUILD_DIR=%PROJECT_ROOT%\build"

echo.
echo ============================================================
echo   Building: %APP_NAME%
echo ============================================================
echo.

REM --- Check Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Please install Python 3.9+ and try again.
    echo.
    echo Press any key to close...
    pause >nul
    exit /b 1
)

REM --- Generate icon if missing ---
if not exist "%ICON_PATH%" (
    echo [INFO] Generating application icon...
    python "%~dp0generate_icon.py"
    if errorlevel 1 (
        echo [WARNING] Icon generation failed. Building without custom icon.
        set "ICON_PATH="
    )
)

REM --- Install/upgrade build dependencies ---
echo [INFO] Installing build dependencies...
pip install --upgrade pyinstaller pyinstaller-hooks-contrib >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller. Check pip and try again.
    echo.
    echo Press any key to close...
    pause >nul
    exit /b 1
)

REM --- Install project dependencies ---
echo [INFO] Installing project dependencies...
pip install -r "%PROJECT_ROOT%\requirements.txt" >nul 2>&1

REM --- Clean previous build ---
if exist "%DIST_DIR%\%EXE_NAME%" (
    echo [INFO] Cleaning previous build...
    rmdir /s /q "%DIST_DIR%\%EXE_NAME%" 2>nul
)

REM --- Build with PyInstaller ---
echo [INFO] Building executable with PyInstaller...
echo.

set "PYINSTALLER_ARGS=--noconfirm --clean --windowed"
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --name "%EXE_NAME%""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --distpath "%DIST_DIR%""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --workpath "%BUILD_DIR%""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --specpath "%BUILD_DIR%""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --paths "%PROJECT_ROOT%\src""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --hidden-import "pg_backup_app""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --hidden-import "pg_backup_app.main_window""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --hidden-import "pg_backup_app.backup_service""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --hidden-import "pg_backup_app.db""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --hidden-import "pg_backup_app.logging_config""

if defined ICON_PATH (
    set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --icon "%ICON_PATH%""
)

pyinstaller %PYINSTALLER_ARGS% "%ENTRY_POINT%"

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the output above for details.
    echo.
    echo Press any key to close...
    pause >nul
    exit /b 1
)

echo.
echo [SUCCESS] Build completed: %DIST_DIR%\%EXE_NAME%\%EXE_NAME%.exe
echo.

REM --- Create Desktop Shortcut ---
echo [INFO] Creating desktop shortcut...

set "DESKTOP=%USERPROFILE%\Desktop"
set "EXE_PATH=%DIST_DIR%\%EXE_NAME%\%EXE_NAME%.exe"
set "SHORTCUT_PATH=%DESKTOP%\%APP_NAME%.lnk"

REM Use PowerShell to create the shortcut
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $sc = $ws.CreateShortcut('%SHORTCUT_PATH%'); ^
     $sc.TargetPath = '%EXE_PATH%'; ^
     $sc.WorkingDirectory = '%DIST_DIR%\%EXE_NAME%'; ^
     $sc.IconLocation = '%EXE_PATH%,0'; ^
     $sc.Description = '%APP_NAME%'; ^
     $sc.Save(); ^
     Write-Host '[SUCCESS] Desktop shortcut created: %SHORTCUT_PATH%'"

if errorlevel 1 (
    echo [WARNING] Could not create desktop shortcut automatically.
    echo           You can manually create a shortcut to: %EXE_PATH%
)

echo.
echo ============================================================
echo   Build Complete!
echo   Executable: %EXE_PATH%
echo   Shortcut:   %SHORTCUT_PATH%
echo ============================================================
echo.

echo.
echo Press any key to close...
pause >nul
exit /b 0
