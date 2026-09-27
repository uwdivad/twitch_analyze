"""Process-wide logging: console output plus a daily rolling log file.

Each process (API, consumer workers, audio capture) writes its own file under
``LOG_DIR`` (``api.log``, ``clickhouse-consumer.log``, ...). Separate files matter:
``TimedRotatingFileHandler`` is not multi-process safe, so two processes rotating the
same file at midnight would clobber each other. Rotated files get a ``.YYYY-MM-DD``
suffix and only the newest ``LOG_RETENTION_DAYS`` are kept.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(process)d] %(name)s: %(message)s"

# Uvicorn installs its own handlers on these loggers and stops propagation to root,
# so the file handler is attached to them directly or server/access logs never
# reach the file.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")
# Chatty third-party loggers that would drown the file at DEBUG.
_QUIET_LOGGERS = ("aiokafka", "kafka", "httpx", "httpcore", "openai", "urllib3")


class _FileHandler(TimedRotatingFileHandler):
    """Marker subclass so a repeated configure_logging() call replaces, not stacks, it."""


class _ConsoleHandler(logging.StreamHandler):
    """Marker subclass, see _FileHandler."""


def configure_logging(settings: Settings, process_name: str) -> Path | None:
    """Install console + daily file handlers on the root logger.

    Returns the log file path, or ``None`` if the log directory is unwritable (the
    process then keeps logging to the console only rather than failing to start).
    """
    level = settings.log_level.upper()
    formatter = logging.Formatter(LOG_FORMAT)
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        # Drop our previous handlers and any bare basicConfig() default, keep
        # anything else (e.g. pytest's capture handler).
        if isinstance(handler, _FileHandler | _ConsoleHandler) or type(handler) is logging.StreamHandler:
            root.removeHandler(handler)
            handler.close()

    console = _ConsoleHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    log_path = Path(settings.log_dir) / f"{process_name}.log"
    file_handler: _FileHandler | None = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = _FileHandler(
            log_path,
            when="midnight",
            backupCount=settings.log_retention_days,
            encoding="utf-8",
            utc=True,
        )
    except OSError:
        root.warning("Cannot write logs to %s; logging to console only", log_path, exc_info=True)
        return None

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        for handler in list(uvicorn_logger.handlers):
            if isinstance(handler, _FileHandler):
                uvicorn_logger.removeHandler(handler)
                handler.close()
        if not uvicorn_logger.propagate:
            uvicorn_logger.addHandler(file_handler)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.getLevelName(level), logging.INFO))

    logging.getLogger(__name__).info(
        "Logging to console and %s (daily rotation, %s days kept)", log_path.resolve(), settings.log_retention_days
    )
    return log_path
