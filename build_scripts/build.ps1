#Requires -Version 5.1
<#
.SYNOPSIS
    Build script for PostgreSQL Backup and Restore application.

.DESCRIPTION
    Builds a standalone Windows executable using PyInstaller and creates
    a desktop shortcut with the application icon.

.PARAMETER Clean
    Remove previous build artifacts before building.

.PARAMETER NoShortcut
    Skip desktop shortcut creation.

.PARAMETER OneFile
    Build as a single .exe file instead of a directory bundle.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean -OneFile
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NoShortcut,
    [switch]$OneFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Configuration ---
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$AppName = "PostgreSQL Backup and Restore"
$ExeName = "PgBackupRestore"
$IconPath = Join-Path $PSScriptRoot "app_icon.ico"
$EntryPoint = Join-Path $ProjectRoot "main.py"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$SrcDir = Join-Path $ProjectRoot "src"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Building: $AppName" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# --- Check Python ---
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[INFO] Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH." -ForegroundColor Red
    Write-Host "        Please install Python 3.9+ and try again." -ForegroundColor Red
    Write-Host ""
    Write-Host "Press any key to close..." -ForegroundColor Gray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

# --- Generate icon if missing ---
if (-not (Test-Path $IconPath)) {
    Write-Host "[INFO] Generating application icon..." -ForegroundColor Yellow
    python (Join-Path $PSScriptRoot "generate_icon.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARNING] Icon generation failed. Building without custom icon." -ForegroundColor Yellow
        $IconPath = $null
    }
}

# --- Install build dependencies ---
Write-Host "[INFO] Installing build dependencies..." -ForegroundColor Yellow
pip install --upgrade pyinstaller pyinstaller-hooks-contrib 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install PyInstaller." -ForegroundColor Red
    Write-Host ""
    Write-Host "Press any key to close..." -ForegroundColor Gray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

# --- Install project dependencies ---
Write-Host "[INFO] Installing project dependencies..." -ForegroundColor Yellow
pip install -r (Join-Path $ProjectRoot "requirements.txt") 2>&1 | Out-Null

# --- Clean previous build ---
if ($Clean -or (Test-Path (Join-Path $DistDir $ExeName))) {
    Write-Host "[INFO] Cleaning previous build artifacts..." -ForegroundColor Yellow
    if (Test-Path (Join-Path $DistDir $ExeName)) {
        Remove-Item -Recurse -Force (Join-Path $DistDir $ExeName)
    }
    if ($Clean -and (Test-Path $BuildDir)) {
        Remove-Item -Recurse -Force $BuildDir
    }
}

# --- Build with PyInstaller ---
Write-Host "[INFO] Building executable with PyInstaller..." -ForegroundColor Yellow
Write-Host ""

$pyinstallerArgs = @(
    "--noconfirm"
    "--clean"
    "--windowed"
    "--name", $ExeName
    "--distpath", $DistDir
    "--workpath", $BuildDir
    "--specpath", $BuildDir
    "--paths", $SrcDir
    "--hidden-import", "pg_backup_app"
    "--hidden-import", "pg_backup_app.main_window"
    "--hidden-import", "pg_backup_app.backup_service"
    "--hidden-import", "pg_backup_app.db"
    "--hidden-import", "pg_backup_app.logging_config"
)

if ($OneFile) {
    $pyinstallerArgs += "--onefile"
}

if ($IconPath -and (Test-Path $IconPath)) {
    $pyinstallerArgs += @("--icon", $IconPath)
}

$pyinstallerArgs += $EntryPoint

& pyinstaller @pyinstallerArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] Build failed. Check the output above for details." -ForegroundColor Red
    Write-Host ""
    Write-Host "Press any key to close..." -ForegroundColor Gray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

# --- Determine exe path ---
if ($OneFile) {
    $ExePath = Join-Path $DistDir "$ExeName.exe"
} else {
    $ExePath = Join-Path $DistDir "$ExeName\$ExeName.exe"
}

Write-Host ""
Write-Host "[SUCCESS] Build completed: $ExePath" -ForegroundColor Green
Write-Host ""

# --- Create Desktop Shortcut ---
if (-not $NoShortcut) {
    Write-Host "[INFO] Creating desktop shortcut..." -ForegroundColor Yellow

    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "$AppName.lnk"

    try {
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $ExePath
        if ($OneFile) {
            $Shortcut.WorkingDirectory = $DistDir
        } else {
            $Shortcut.WorkingDirectory = Join-Path $DistDir $ExeName
        }
        $Shortcut.IconLocation = "$ExePath,0"
        $Shortcut.Description = $AppName
        $Shortcut.Save()

        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($Shortcut) | Out-Null
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($WshShell) | Out-Null

        Write-Host "[SUCCESS] Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
    } catch {
        Write-Host "[WARNING] Could not create desktop shortcut: $_" -ForegroundColor Yellow
        Write-Host "          You can manually create a shortcut to: $ExePath" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Build Complete!" -ForegroundColor Cyan
Write-Host "  Executable: $ExePath" -ForegroundColor Cyan
if (-not $NoShortcut) {
    Write-Host "  Shortcut:   $ShortcutPath" -ForegroundColor Cyan
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press any key to close..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
