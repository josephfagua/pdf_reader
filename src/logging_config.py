"""
logging_config.py — Centralised logging setup for MDIP.
"""

import logging
import logging.handlers
import os
import pathlib

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(user)s | %(invoice_number)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3

_LOCAL_FALLBACK_DIR = pathlib.Path(
    os.environ.get("LOCALAPPDATA", "~")
).expanduser() / "MD Invoice Processor" / "logs"

_LOCAL_FALLBACK_PATH = _LOCAL_FALLBACK_DIR / "app.log"
_logger_ready = False


def setup_logging(log_path: str | None = None) -> None:
    global _logger_ready

    if _logger_ready:
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in _build_handlers(log_path):
        handler.setFormatter(_FORMATTER)
        root.addHandler(handler)

    _logger_ready = True


def get_logger(name: str = "mdip") -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    user: str,
    machine: str,
    invoice_number: str,
    file_size_kb: float,
    duration_s: float,
    item_count: int,
    status: str,
    detail: str,
) -> None:
    message = (
        f"User: {user} | "
        f"Machine: {machine} | "
        f"Invoice: {invoice_number} | "
        f"Size: {file_size_kb} KB | "
        f"Duration: {duration_s}s | "
        f"Items: {item_count} | "
        f"Status: {status} | "
        f"{'Output' if status == 'SUCCESS' else 'Detail'}: {detail}"
    )

    extra = {"user": user, "invoice_number": invoice_number}

    if status == "ERROR":
        logger.error(message, extra=extra)
    else:
        logger.info(message, extra=extra)


def _make_rotating_handler(
    path: pathlib.Path,
) -> logging.handlers.RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)

    return logging.handlers.RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )


def _build_handlers(
    log_path: str | None,
) -> list[logging.handlers.RotatingFileHandler]:
    handlers = []

    try:
        handlers.append(_make_rotating_handler(_LOCAL_FALLBACK_PATH))
    except Exception:
        pass

    if log_path and log_path.strip():
        try:
            handlers.append(
                _make_rotating_handler(pathlib.Path(log_path))
            )
        except Exception:
            pass

    return handlers
