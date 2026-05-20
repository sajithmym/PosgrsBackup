from .logging_config import configure_console_logging
from .main_window import run_app


if __name__ == "__main__":
    configure_console_logging()
    run_app()
