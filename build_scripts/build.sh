#!/usr/bin/env bash
# ============================================================================
#  PostgreSQL Backup and Restore - Linux/macOS Build Script
#  Builds a standalone executable using PyInstaller and creates a desktop entry.
# ============================================================================

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="PostgreSQL Backup and Restore"
EXE_NAME="PgBackupRestore"
ICON_PATH="$SCRIPT_DIR/app_icon.ico"
ENTRY_POINT="$PROJECT_ROOT/main.py"
DIST_DIR="$PROJECT_ROOT/dist"
BUILD_DIR="$PROJECT_ROOT/build"
SRC_DIR="$PROJECT_ROOT/src"

# --- Parse arguments ---
CLEAN=false
NO_SHORTCUT=false
ONE_FILE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)    CLEAN=true; shift ;;
        --no-shortcut) NO_SHORTCUT=true; shift ;;
        --onefile)  ONE_FILE=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--clean] [--no-shortcut] [--onefile]"
            echo ""
            echo "Options:"
            echo "  --clean        Remove previous build artifacts before building"
            echo "  --no-shortcut  Skip desktop shortcut/entry creation"
            echo "  --onefile      Build as a single executable file"
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown option: $1"
            exit 1
            ;;
    esac
done

echo ""
echo "============================================================"
echo "  Building: $APP_NAME"
echo "============================================================"
echo ""

# --- Check Python ---
if ! command -v python3 &>/dev/null; then
    if ! command -v python &>/dev/null; then
        echo "[ERROR] Python is not installed or not in PATH."
        echo "        Please install Python 3.9+ and try again."
        exit 1
    fi
    PYTHON=python
else
    PYTHON=python3
fi

PYTHON_VERSION=$($PYTHON --version 2>&1)
echo "[INFO] Python found: $PYTHON_VERSION"

# --- Determine pip ---
if command -v pip3 &>/dev/null; then
    PIP=pip3
elif command -v pip &>/dev/null; then
    PIP=pip
else
    PIP="$PYTHON -m pip"
fi

# --- Generate icon if missing ---
if [[ ! -f "$ICON_PATH" ]]; then
    echo "[INFO] Generating application icon..."
    $PYTHON "$SCRIPT_DIR/generate_icon.py" || {
        echo "[WARNING] Icon generation failed. Building without custom icon."
        ICON_PATH=""
    }
fi

# --- Install build dependencies ---
echo "[INFO] Installing build dependencies..."
$PIP install --upgrade pyinstaller pyinstaller-hooks-contrib >/dev/null 2>&1 || {
    echo "[ERROR] Failed to install PyInstaller."
    exit 1
}

# --- Install project dependencies ---
echo "[INFO] Installing project dependencies..."
$PIP install -r "$PROJECT_ROOT/requirements.txt" >/dev/null 2>&1 || true

# --- Clean previous build ---
if [[ "$CLEAN" == true ]] || [[ -d "$DIST_DIR/$EXE_NAME" ]]; then
    echo "[INFO] Cleaning previous build artifacts..."
    rm -rf "$DIST_DIR/$EXE_NAME" 2>/dev/null || true
    if [[ "$CLEAN" == true ]]; then
        rm -rf "$BUILD_DIR" 2>/dev/null || true
    fi
fi

# --- Build with PyInstaller ---
echo "[INFO] Building executable with PyInstaller..."
echo ""

PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --windowed
    --name "$EXE_NAME"
    --distpath "$DIST_DIR"
    --workpath "$BUILD_DIR"
    --specpath "$BUILD_DIR"
    --paths "$SRC_DIR"
    --hidden-import "pg_backup_app"
    --hidden-import "pg_backup_app.main_window"
    --hidden-import "pg_backup_app.backup_service"
    --hidden-import "pg_backup_app.db"
    --hidden-import "pg_backup_app.logging_config"
)

if [[ "$ONE_FILE" == true ]]; then
    PYINSTALLER_ARGS+=(--onefile)
fi

if [[ -n "$ICON_PATH" && -f "$ICON_PATH" ]]; then
    PYINSTALLER_ARGS+=(--icon "$ICON_PATH")
fi

PYINSTALLER_ARGS+=("$ENTRY_POINT")

pyinstaller "${PYINSTALLER_ARGS[@]}"

if [[ $? -ne 0 ]]; then
    echo ""
    echo "[ERROR] Build failed. Check the output above for details."
    exit 1
fi

# --- Determine exe path ---
if [[ "$ONE_FILE" == true ]]; then
    EXE_PATH="$DIST_DIR/$EXE_NAME"
else
    EXE_PATH="$DIST_DIR/$EXE_NAME/$EXE_NAME"
fi

echo ""
echo "[SUCCESS] Build completed: $EXE_PATH"
echo ""

# --- Create Desktop Entry (Linux) / Alias (macOS) ---
if [[ "$NO_SHORTCUT" == false ]]; then
    if [[ "$(uname)" == "Linux" ]]; then
        echo "[INFO] Creating desktop entry..."
        DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
        DESKTOP_FILE="$DESKTOP_DIR/pg-backup-restore.desktop"

        mkdir -p "$DESKTOP_DIR"
        cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_NAME
Comment=PostgreSQL database backup and restore utility
Exec=$EXE_PATH
Icon=$ICON_PATH
Terminal=false
Categories=Development;Database;Utility;
StartupNotify=true
EOF
        chmod +x "$DESKTOP_FILE"
        echo "[SUCCESS] Desktop entry created: $DESKTOP_FILE"

        # Also install to applications menu
        APPS_DIR="$HOME/.local/share/applications"
        mkdir -p "$APPS_DIR"
        cp "$DESKTOP_FILE" "$APPS_DIR/pg-backup-restore.desktop"
        echo "[INFO] Application menu entry installed."

    elif [[ "$(uname)" == "Darwin" ]]; then
        echo "[INFO] Creating macOS alias on Desktop..."
        DESKTOP_DIR="$HOME/Desktop"
        if [[ -f "$EXE_PATH" ]]; then
            ln -sf "$EXE_PATH" "$DESKTOP_DIR/$APP_NAME"
            echo "[SUCCESS] Desktop alias created: $DESKTOP_DIR/$APP_NAME"
        else
            echo "[WARNING] Could not create desktop alias. Executable not found at: $EXE_PATH"
        fi
    fi
fi

echo ""
echo "============================================================"
echo "  Build Complete!"
echo "  Executable: $EXE_PATH"
if [[ "$NO_SHORTCUT" == false ]]; then
    echo "  Desktop entry created."
fi
echo "============================================================"
echo ""
