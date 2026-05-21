# Build Scripts

Build scripts for packaging **PostgreSQL Backup and Restore** as a standalone executable.

## Prerequisites

- Python 3.9+
- pip (for installing dependencies)
- PyInstaller (installed automatically by the scripts)

## Usage

### Windows (CMD)

```cmd
build_scripts\build.bat
```

### Windows (PowerShell)

```powershell
.\build_scripts\build.ps1

# Options:
.\build_scripts\build.ps1 -Clean          # Clean build
.\build_scripts\build.ps1 -OneFile         # Single .exe file
.\build_scripts\build.ps1 -NoShortcut      # Skip desktop shortcut
.\build_scripts\build.ps1 -Clean -OneFile  # Combined
```

### Linux / macOS

```bash
chmod +x build_scripts/build.sh
./build_scripts/build.sh

# Options:
./build_scripts/build.sh --clean          # Clean build
./build_scripts/build.sh --onefile        # Single executable
./build_scripts/build.sh --no-shortcut    # Skip desktop entry
```

## Output

| Item | Location |
|------|----------|
| Executable | `dist/PgBackupRestore/PgBackupRestore.exe` |
| Single-file exe | `dist/PgBackupRestore.exe` (with `--onefile`) |
| Desktop shortcut | User's Desktop folder |
| Icon | `build_scripts/app_icon.ico` |

## Files

| File | Purpose |
|------|---------|
| `build.bat` | Windows CMD build script |
| `build.ps1` | PowerShell build script (recommended on Windows) |
| `build.sh` | Linux/macOS bash build script |
| `generate_icon.py` | Generates the application `.ico` file |
| `app_icon.ico` | Generated application icon (multi-resolution) |

## Notes

- The scripts automatically install PyInstaller and project dependencies.
- Desktop shortcuts are created automatically unless `--no-shortcut` is specified.
- The `--onefile` option produces a single portable `.exe` but has slower startup time.
- Default directory mode is recommended for faster startup and easier debugging.
