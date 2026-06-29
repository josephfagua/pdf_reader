"""
logging_config.py — Centralised logging setup for MDIP.

Call setup_logging(log_path) once at application startup (from MDIP.py).
After that, any module can do:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("...")

Log destinations (dual-write):
  1. Local  — always: %LOCALAPPDATA%\\MD Invoice Processor\\logs\\app.log
  2. Network — when configured: the UNC path stored in config.json ("log_path")

The local log is guaranteed on every machine.  The network log gives management
a central view.  A network failure never affects the local log or the app.

Log line format (pipe-separated for easy manual reading / grep):
    2025-06-29 14:03:00 | DOMAIN\\username | C:\\path\\to\\invoice.pdf | SUCCESS: C:\\out\\invoice.csv
    2025-06-29 14:05:12 | DOMAIN\\username | C:\\path\\to\\bad.pdf     | ERROR: <message>
"""

import logging
import logging.handlers
import os
import pathlib

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(user)s | %(invoice_file)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_MAX_BYTES = 1_000_000   # 1 MB per file
_BACKUP_COUNT = 3        # keep app.log, app.log.1, app.log.2, app.log.3

_LOCAL_FALLBACK_DIR = pathlib.Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() \
                      / "MD Invoice Processor" / "logs"
_LOCAL_FALLBACK_PATH = _LOCAL_FALLBACK_DIR / "app.log"

_logger_ready = False   # guard against double-initialisation


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(log_path: str | None = None) -> None:
    """
    Initialise the root logger with a RotatingFileHandler.

    Parameters
    ----------
    log_path:
        Full path (file, not just directory) to the desired log file.
        Typically the value of config["log_path"], which should be a UNC
        path such as ``\\\\SERVER\\MDIPLogs\\app.log``.
        If None, empty, or unreachable, falls back to the local path.
    """
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
    """Return a named logger.  setup_logging() must have been called first."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    user: str,
    machine: str,
    invoice_file: str,
    file_size_kb: float,
    duration_s: float,
    item_count: int,
    status: str,
    detail: str,
) -> None:
    """
    Write a single structured log line.

    Parameters
    ----------
    logger:        Logger instance (from get_logger()).
    user:          Windows username (e.g. "jsmith").
    machine:       Windows computer name (e.g. "DESKTOP-A1B2").
    invoice_file:  Filename only (with extension) of the PDF attempted.
    file_size_kb:  Size of the PDF in kilobytes, rounded to 1 dp.
    duration_s:    Total processing time in seconds, rounded to 1 dp.
    item_count:    Number of line items successfully extracted (0 on error).
    status:        "SUCCESS" or "ERROR".
    detail:        Output CSV filename on success, or the error message on failure.
    """
    message = (
        f"User: {user} | "
        f"Machine: {machine} | "
        f"Invoice: {invoice_file} | "
        f"Size: {file_size_kb} KB | "
        f"Duration: {duration_s}s | "
        f"Items: {item_count} | "
        f"Status: {status} | "
        f"{'Output' if status == 'SUCCESS' else 'Detail'}: {detail}"
    )
    extra = {"user": user, "invoice_file": invoice_file}
    if status == "ERROR":
        logger.error(message, extra=extra)
    else:
        logger.info(message, extra=extra)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_rotating_handler(path: pathlib.Path) -> logging.handlers.RotatingFileHandler:
    """Create a RotatingFileHandler, ensuring the parent directory exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return logging.handlers.RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )


def _build_handlers(log_path: str | None) -> list[logging.handlers.RotatingFileHandler]:
    """
    Always returns at least one handler (local).  If a network log_path is
    configured and reachable, a second handler for that path is also returned
    so every log line is written to both destinations simultaneously.

    Dual-write means:
      - Local log  → guaranteed, available immediately on the machine for dev/debug.
      - Network log → authoritative record management can read centrally.

    A failure creating the network handler is silently ignored — the app must
    never crash because a share is temporarily unreachable.
    """
    handlers = []

    # ── Local handler — always created ───────────────────────────────
    try:
        handlers.append(_make_rotating_handler(_LOCAL_FALLBACK_PATH))
    except Exception:
        pass  # extremely unlikely; nothing we can do if LOCALAPPDATA itself is broken

    # ── Network handler — created only when log_path is configured ───
    if log_path and log_path.strip():
        try:
            handlers.append(_make_rotating_handler(pathlib.Path(log_path)))
        except Exception:
            pass  # share unreachable or permissions issue — local log still works

    return handlers