#!/usr/bin/env bash
# ============================================================================
#  PostgreSQL Backup and Restore - Linux/macOS Build Script
#  Builds a standalone executable using PyInstaller and creates a desktop entry.
# ============================================================================

set -uo pipefail

# ============================================================================
#  Helper Functions
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m'

info()    { echo -e "${YELLOW}[INFO]${NC} $1"; }
ok()      { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
err()     { echo -e "${RED}[ERROR]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARNING]${NC} $1"; }

wait_and_exit() {
    echo ""
    echo -e "${GRAY}Press any key to close...${NC}"
    read -n 1 -s -r
    exit "$1"
}

# ============================================================================
#  Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="PostgreSQL Backup and Restore"
EXE_NAME="PgBackupRestore"
ICON_PATH="$SCRIPT_DIR/app_icon.ico"
ENTRY_POINT="$PROJECT_ROOT/main.py"
DIST_DIR="$PROJECT_ROOT/dist"
BUILD_DIR="$PROJECT_ROOT/build"
SRC_DIR="$PROJECT_ROOT/src"

# ============================================================================
#  Parse Arguments
# ============================================================================

CLEAN=false
NO_SHORTCUT=false
ONE_FILE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)       CLEAN=true; shift ;;
        --no-shortcut) NO_SHORTCUT=true; shift ;;
        --onefile)     ONE_FILE=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--clean] [--no-shortcut] [--onefile]"
            echo ""
            echo "Options:"
            echo "  --clean        Remove previous build artifacts before building"
            echo "  --no-shortcut  Skip desktop entry creation"
            echo "  --onefile      Build as a single executable file"
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
#  Build Pipeline
# ============================================================================

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Building: $APP_NAME${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# --- 1. Check Python ---
info "Checking Python installation..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    err "Python is not installed or not in PATH."
    err "Please install Python 3.9+ and try again."
    wait_and_exit 1
fi

PYTHON_VERSION=$($PYTHON --version 2>&1)
ok "Python found: $PYTHON_VERSION"

# --- Determine pip ---
if command -v pip3 &>/dev/null; then
    PIP=pip3
elif command -v pip &>/dev/null; then
    PIP=pip
else
    PIP="$PYTHON -m pip"
fi

# --- 2. Generate icon if missing ---
if [[ ! -f "$ICON_PATH" ]]; then
    info "Generating application icon..."
    if $PYTHON "$SCRIPT_DIR/generate_icon.py"; then
        ok "Icon generated."
    else
        warn "Icon generation failed. Building without custom icon."
        ICON_PATH=""
    fi
fi

# --- 3. Install build dependencies ---
info "Installing build dependencies (PyInstaller)..."
if $PIP install --upgrade --quiet pyinstaller pyinstaller-hooks-contrib >/dev/null 2>&1; then
    ok "Build dependencies ready."
else
    err "Failed to install PyInstaller. Check your pip configuration."
    wait_and_exit 1
fi

# --- 4. Install project dependencies ---
info "Installing project dependencies..."
if $PIP install --quiet -r "$PROJECT_ROOT/requirements.txt" >/dev/null 2>&1; then
    ok "Project dependencies ready."
else
    warn "Some project dependencies may have failed to install."
fi

# --- 5. Clean previous build ---
if [[ "$CLEAN" == true ]] || [[ -d "$DIST_DIR/$EXE_NAME" ]]; then
    info "Cleaning previous build artifacts..."
    rm -rf "$DIST_DIR/$EXE_NAME" 2>/dev/null || true
    if [[ "$CLEAN" == true ]]; then
        rm -rf "$BUILD_DIR" 2>/dev/null || true
    fi
    ok "Clean complete."
fi

# --- 6. Build with PyInstaller ---
info "Building executable with PyInstaller..."
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

if ! pyinstaller "${PYINSTALLER_ARGS[@]}"; then
    echo ""
    err "Build failed. Check the PyInstaller output above for details."
    wait_and_exit 1
fi

# --- 7. Verify output ---
if [[ "$ONE_FILE" == true ]]; then
    EXE_PATH="$DIST_DIR/$EXE_NAME"
else
    EXE_PATH="$DIST_DIR/$EXE_NAME/$EXE_NAME"
fi

if [[ ! -f "$EXE_PATH" ]]; then
    err "Build appeared to succeed but executable not found at: $EXE_PATH"
    wait_and_exit 1
fi

echo ""
ok "Build completed: $EXE_PATH"
echo ""

# --- 8. Create Desktop Entry (Linux) / Alias (macOS) ---
if [[ "$NO_SHORTCUT" == false ]]; then
    if [[ "$(uname)" == "Linux" ]]; then
        info "Creating desktop entry..."
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
        ok "Desktop entry created: $DESKTOP_FILE"

        # Also install to applications menu
        APPS_DIR="$HOME/.local/share/applications"
        mkdir -p "$APPS_DIR"
        cp "$DESKTOP_FILE" "$APPS_DIR/pg-backup-restore.desktop"
        info "Application menu entry installed."

    elif [[ "$(uname)" == "Darwin" ]]; then
        info "Creating macOS alias on Desktop..."
        DESKTOP_DIR="$HOME/Desktop"
        if ln -sf "$EXE_PATH" "$DESKTOP_DIR/$APP_NAME" 2>/dev/null; then
            ok "Desktop alias created: $DESKTOP_DIR/$APP_NAME"
        else
            warn "Could not create desktop alias."
        fi
    fi
fi

# ============================================================================
#  Done
# ============================================================================

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Build Complete!${NC}"
echo -e "${CYAN}  Executable: $EXE_PATH${NC}"
if [[ "$NO_SHORTCUT" == false ]]; then
    echo -e "${CYAN}  Desktop entry created.${NC}"
fi
echo -e "${CYAN}============================================================${NC}"

wait_and_exit 0
