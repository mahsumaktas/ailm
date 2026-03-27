"""Structured logging setup — console + rotating file."""

import logging
import logging.handlers
from pathlib import Path

_CONSOLE_FORMAT = "%(levelname)-8s %(name)s: %(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(funcName)s:%(lineno)d] %(message)s"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3

_DEFAULT_LOG_DIR = Path.home() / ".local" / "share" / "ailm"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure logging: console + rotating file.

    Args:
        level: Root log level for console output (default INFO).
        log_dir: Directory for log file. Defaults to ~/.local/share/ailm/.
    """
    root = logging.getLogger()

    # Avoid duplicate handlers on repeated calls
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)

    # --- Console handler (user-facing, concise) ---
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root.addHandler(console)

    # --- Rotating file handler (debug-level, verbose) ---
    log_path = (log_dir or _DEFAULT_LOG_DIR) / "ailm.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    root.addHandler(file_handler)
