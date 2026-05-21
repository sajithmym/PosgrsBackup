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

# ============================================================================
#  Helper Functions
# ============================================================================

function Write-Step([string]$Message) {
    Write-Host "[INFO] $Message" -ForegroundColor Yellow
}

function Write-Ok([string]$Message) {
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Err([string]$Message) {
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-Warn([string]$Message) {
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Wait-AndExit([int]$Code) {
    Write-Host ""
    Write-Host "Press any key to close..." -ForegroundColor Gray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit $Code
}

function Invoke-External([string]$Command, [string[]]$Arguments, [switch]$Silent) {
    <#
    .SYNOPSIS
        Runs an external command safely without PowerShell treating stderr as errors.
    #>
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Silent) {
            & $Command @Arguments 2>&1 | Out-Null
        } else {
            & $Command @Arguments
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevPref
    }
}

# ============================================================================
#  Configuration
# ============================================================================

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$AppName     = "PostgreSQL Backup and Restore"
$ExeName     = "PgBackupRestore"
$IconPath    = Join-Path $PSScriptRoot "app_icon.ico"
$EntryPoint  = Join-Path $ProjectRoot "main.py"
$DistDir     = Join-Path $ProjectRoot "dist"
$BuildDir    = Join-Path $ProjectRoot "build"
$SrcDir      = Join-Path $ProjectRoot "src"

# ============================================================================
#  Build Pipeline
# ============================================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Building: $AppName" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Check Python ---
Write-Step "Checking Python installation..."
$exitCode = Invoke-External "python" @("--version") -Silent
if ($exitCode -ne 0) {
    Write-Err "Python is not installed or not in PATH."
    Write-Err "Please install Python 3.9+ and try again."
    Wait-AndExit 1
}
$pythonVersion = & python --version 2>&1
Write-Ok "Python found: $pythonVersion"

# --- 2. Generate icon if missing ---
if (-not (Test-Path $IconPath)) {
    Write-Step "Generating application icon..."
    $exitCode = Invoke-External "python" @((Join-Path $PSScriptRoot "generate_icon.py"))
    if ($exitCode -ne 0) {
        Write-Warn "Icon generation failed. Building without custom icon."
        $IconPath = $null
    }
}

# --- 3. Install build dependencies ---
Write-Step "Installing build dependencies (PyInstaller)..."
$exitCode = Invoke-External "pip" @("install", "--upgrade", "--quiet", "pyinstaller", "pyinstaller-hooks-contrib") -Silent
if ($exitCode -ne 0) {
    Write-Err "Failed to install PyInstaller. Check your pip configuration."
    Wait-AndExit 1
}
Write-Ok "Build dependencies ready."

# --- 4. Install project dependencies ---
Write-Step "Installing project dependencies..."
$requirementsFile = Join-Path $ProjectRoot "requirements.txt"
$exitCode = Invoke-External "pip" @("install", "--quiet", "-r", $requirementsFile) -Silent
if ($exitCode -ne 0) {
    Write-Warn "Some project dependencies may have failed to install."
}
Write-Ok "Project dependencies ready."

# --- 5. Clean previous build ---
if ($Clean -or (Test-Path (Join-Path $DistDir $ExeName))) {
    Write-Step "Cleaning previous build artifacts..."
    $targetDist = Join-Path $DistDir $ExeName
    if (Test-Path $targetDist) {
        Remove-Item -Recurse -Force $targetDist
    }
    if ($Clean -and (Test-Path $BuildDir)) {
        Remove-Item -Recurse -Force $BuildDir
    }
    Write-Ok "Clean complete."
}

# --- 6. Build with PyInstaller ---
Write-Step "Building executable with PyInstaller..."
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

$exitCode = Invoke-External "pyinstaller" $pyinstallerArgs
if ($exitCode -ne 0) {
    Write-Host ""
    Write-Err "Build failed. Check the PyInstaller output above for details."
    Wait-AndExit 1
}

# --- 7. Determine output path ---
if ($OneFile) {
    $ExePath = Join-Path $DistDir "$ExeName.exe"
} else {
    $ExePath = Join-Path $DistDir "$ExeName\$ExeName.exe"
}

if (-not (Test-Path $ExePath)) {
    Write-Err "Build appeared to succeed but executable not found at: $ExePath"
    Wait-AndExit 1
}

Write-Host ""
Write-Ok "Build completed: $ExePath"
Write-Host ""

# --- 8. Create Desktop Shortcut ---
if (-not $NoShortcut) {
    Write-Step "Creating desktop shortcut..."

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

        Write-Ok "Desktop shortcut created: $ShortcutPath"
    } catch {
        Write-Warn "Could not create desktop shortcut: $_"
        Write-Warn "You can manually create a shortcut to: $ExePath"
    }
}

# ============================================================================
#  Done
# ============================================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Build Complete!" -ForegroundColor Cyan
Write-Host "  Executable: $ExePath" -ForegroundColor Cyan
if (-not $NoShortcut) {
    Write-Host "  Shortcut:   $ShortcutPath" -ForegroundColor Cyan
}
Write-Host "============================================================" -ForegroundColor Cyan

Wait-AndExit 0
