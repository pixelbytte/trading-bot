"""
Central logger for the bot.
Writes to console + DuckDB + a rotating text log file.
Use this instead of print() everywhere.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from data.db import log_error

# Set up the log file location
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"

# Build the Python logger
_logger = logging.getLogger("trading_bot")
_logger.setLevel(logging.DEBUG)

# Only add handlers once (prevents duplicate logs on re-import)
if not _logger.handlers:
    _formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _file_handler = logging.FileHandler(LOG_FILE)
    _file_handler.setFormatter(_formatter)
    _logger.addHandler(_file_handler)

    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(_formatter)
    _logger.addHandler(_console_handler)


def info(message, source=""):
    """Routine info — startup, signals, decisions."""
    tag = f"[{source}] " if source else ""
    _logger.info(f"{tag}{message}")


def warning(message, source=""):
    """Something noteworthy but not broken."""
    tag = f"[{source}] " if source else ""
    _logger.warning(f"{tag}{message}")


def error(message, source="", exc=None):
    """Something failed. Logs to file + DB."""
    tag = f"[{source}] " if source else ""
    _logger.error(f"{tag}{message}")
    stacktrace = ""
    if exc is not None:
        import traceback
        stacktrace = traceback.format_exc()
    log_error(source=source or "unknown", message=message, stacktrace=stacktrace)


def debug(message, source=""):
    """Verbose debugging — usually filtered out."""
    tag = f"[{source}] " if source else ""
    _logger.debug(f"{tag}{message}")