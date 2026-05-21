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
set "SRC_DIR=%PROJECT_ROOT%\src"

echo.
echo ============================================================
echo   Building: %APP_NAME%
echo ============================================================
echo.

REM --- 1. Check Python ---
echo [INFO] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Please install Python 3.9+ and try again.
    goto :fail
)
for /f "delims=" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
echo [SUCCESS] Python found: %PY_VER%

REM --- 2. Generate icon if missing ---
if not exist "%ICON_PATH%" (
    echo [INFO] Generating application icon...
    python "%~dp0generate_icon.py"
    if errorlevel 1 (
        echo [WARNING] Icon generation failed. Building without custom icon.
        set "ICON_PATH="
    )
)

REM --- 3. Install build dependencies ---
echo [INFO] Installing build dependencies (PyInstaller)...
pip install --upgrade --quiet pyinstaller pyinstaller-hooks-contrib >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller. Check your pip configuration.
    goto :fail
)
echo [SUCCESS] Build dependencies ready.

REM --- 4. Install project dependencies ---
echo [INFO] Installing project dependencies...
pip install --quiet -r "%PROJECT_ROOT%\requirements.txt" >nul 2>&1
echo [SUCCESS] Project dependencies ready.

REM --- 5. Clean previous build ---
if exist "%DIST_DIR%\%EXE_NAME%" (
    echo [INFO] Cleaning previous build...
    rmdir /s /q "%DIST_DIR%\%EXE_NAME%" 2>nul
    echo [SUCCESS] Clean complete.
)

REM --- 6. Build with PyInstaller ---
echo [INFO] Building executable with PyInstaller...
echo.

set "ARGS=--noconfirm --clean --windowed"
set "ARGS=%ARGS% --name %EXE_NAME%"
set "ARGS=%ARGS% --distpath "%DIST_DIR%""
set "ARGS=%ARGS% --workpath "%BUILD_DIR%""
set "ARGS=%ARGS% --specpath "%BUILD_DIR%""
set "ARGS=%ARGS% --paths "%SRC_DIR%""
set "ARGS=%ARGS% --hidden-import pg_backup_app"
set "ARGS=%ARGS% --hidden-import pg_backup_app.main_window"
set "ARGS=%ARGS% --hidden-import pg_backup_app.backup_service"
set "ARGS=%ARGS% --hidden-import pg_backup_app.db"
set "ARGS=%ARGS% --hidden-import pg_backup_app.logging_config"

if defined ICON_PATH (
    if exist "%ICON_PATH%" (
        set "ARGS=%ARGS% --icon "%ICON_PATH%""
    )
)

pyinstaller %ARGS% "%ENTRY_POINT%"

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the PyInstaller output above for details.
    goto :fail
)

REM --- 7. Verify output ---
set "EXE_PATH=%DIST_DIR%\%EXE_NAME%\%EXE_NAME%.exe"
if not exist "%EXE_PATH%" (
    echo [ERROR] Build appeared to succeed but executable not found.
    goto :fail
)

echo.
echo [SUCCESS] Build completed: %EXE_PATH%
echo.

REM --- 8. Create Desktop Shortcut ---
echo [INFO] Creating desktop shortcut...

set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT_PATH=%DESKTOP%\%APP_NAME%.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell;" ^
    "$sc = $ws.CreateShortcut('%SHORTCUT_PATH%');" ^
    "$sc.TargetPath = '%EXE_PATH%';" ^
    "$sc.WorkingDirectory = '%DIST_DIR%\%EXE_NAME%';" ^
    "$sc.IconLocation = '%EXE_PATH%,0';" ^
    "$sc.Description = '%APP_NAME%';" ^
    "$sc.Save()"

if errorlevel 1 (
    echo [WARNING] Could not create desktop shortcut automatically.
    echo           You can manually create a shortcut to: %EXE_PATH%
) else (
    echo [SUCCESS] Desktop shortcut created: %SHORTCUT_PATH%
)

REM --- Done ---
echo.
echo ============================================================
echo   Build Complete!
echo   Executable: %EXE_PATH%
echo   Shortcut:   %SHORTCUT_PATH%
echo ============================================================
echo.
echo Press any key to close...
pause >nul
exit /b 0

:fail
echo.
echo Press any key to close...
pause >nul
exit /b 1
