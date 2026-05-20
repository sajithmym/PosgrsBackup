from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pg_backup_app.logging_config import configure_console_logging
from pg_backup_app.main_window import run_app


if __name__ == "__main__":
    configure_console_logging()
    run_app()
