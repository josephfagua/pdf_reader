"""logging_config.py — Centralised logging setup for MDIP."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import pathlib
import traceback
from typing import Any


_FORMATTER = logging.Formatter(
    fmt=(
        "%(asctime)s | User: %(user)s | Machine: %(machine)s | "
        "Invoice: %(invoice_number)s | %(message)s"
    ),
    datefmt="%Y-%m-%d %H:%M:%S",
)

_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3

_LOCAL_FALLBACK_DIR = pathlib.Path(
    os.environ.get("LOCALAPPDATA", "~")
).expanduser() / "MD Invoice Processor" / "logs"

_LOCAL_FALLBACK_PATH = _LOCAL_FALLBACK_DIR / "app.log"
_logger_ready = False


class _ContextFilter(logging.Filter):
    """Ensure the formatter always has the fields it expects."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.user = getattr(record, "user", "SYSTEM")
        record.machine = getattr(record, "machine", "UNKNOWN")
        record.invoice_number = getattr(record, "invoice_number", "SYSTEM")
        return True


def _remove_mdip_handlers(root: logging.Logger) -> None:
    """Remove handlers created by MDIP so logging can be reconfigured."""
    for handler in list(root.handlers):
        if getattr(handler, "_mdip_handler", False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def setup_logging(audit_log_path: str | None = None) -> None:
    """Configure local fallback and optional shared audit logging."""
    global _logger_ready

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # setup_logging may be called more than once when an administrator saves
    # settings while the application is running. Replacing only MDIP's own
    # handlers keeps the configuration deterministic without disturbing
    # unrelated Python logging handlers.
    _remove_mdip_handlers(root)

    context_filter = _ContextFilter()

    for handler in _build_handlers(audit_log_path):
        handler.setFormatter(_FORMATTER)
        handler.addFilter(context_filter)
        handler._mdip_handler = True
        root.addHandler(handler)

    _logger_ready = True


def reconfigure_logging(audit_log_path: str | None = None) -> None:
    """Apply a new administrator audit-log path without restarting MDIP."""
    setup_logging(audit_log_path)


def get_logger(name: str = "mdip") -> logging.Logger:
    return logging.getLogger(name)


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


def _make_audit_handler(path: pathlib.Path) -> logging.FileHandler:
    """Create the shared audit handler without client-side rotation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return logging.FileHandler(path, encoding="utf-8")


def _build_handlers(
    audit_log_path: str | None,
) -> list[logging.Handler]:
    """Build local fallback and optional shared audit handlers."""
    handlers: list[logging.Handler] = []

    try:
        handlers.append(_make_rotating_handler(_LOCAL_FALLBACK_PATH))
    except Exception:
        pass

    if audit_log_path and audit_log_path.strip():
        try:
            handlers.append(_make_audit_handler(pathlib.Path(audit_log_path)))
        except Exception:
            pass

    if not handlers:
        handlers.append(logging.NullHandler())

    return handlers


def log_batch_event(
    logger: logging.Logger,
    *,
    user: str,
    machine: str,
    batch_id: str,
    invoices: list[dict[str, Any]],
    exceptions: list[dict[str, Any]] | None = None,
) -> None:
    """Write one successful batch event with all invoices and exceptions."""
    exceptions = exceptions or []
    clients = []
    seen_clients: set[str] = set()

    for invoice in invoices:
        client = str(invoice.get("client") or "UNKNOWN").strip()
        if client not in seen_clients:
            clients.append(client)
            seen_clients.add(client)

    payload = {
        "event": "BATCH_SUCCESS",
        "batch_id": batch_id,
        "invoice_count": len(invoices),
        "clients": clients,
        "invoices": invoices,
        "approved_exceptions": exceptions,
    }

    message = "Batch completed successfully | " + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    logger.info(
        message,
        extra={
            "user": user,
            "machine": machine,
            "invoice_number": "BATCH",
        },
    )


def log_batch_error(
    logger: logging.Logger,
    *,
    user: str,
    machine: str,
    invoices: list[dict[str, Any]],
    exceptions: list[dict[str, Any]] | None,
    failed_invoice: str,
    detail: str,
) -> None:
    """Write a batch-failure event while preserving exception details."""
    payload = {
        "event": "BATCH_ERROR",
        "invoice_count_requested": len(invoices),
        "failed_invoice": failed_invoice,
        "approved_exceptions": exceptions or [],
        "detail": detail,
    }

    logger.error(
        "Batch processing failed | " + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        extra={
            "user": user,
            "machine": machine,
            "invoice_number": failed_invoice or "UNKNOWN",
        },
    )


def log_invoice_error(
    logger: logging.Logger,
    *,
    user: str,
    machine: str,
    invoice_number: str,
    detail: str,
    exc: BaseException | None = None,
) -> None:
    """Write an invoice-level error with exception type and traceback."""
    exception_detail = ""
    traceback_detail = ""

    if exc is not None:
        exception_detail = f"{type(exc).__name__}: {exc}"
        traceback_detail = "\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).rstrip()

    message = (
        f"Invoice processing failed | Detail: {detail}"
        + (f" | Exception: {exception_detail}" if exception_detail else "")
        + (f" | Traceback: {traceback_detail}" if traceback_detail else "")
    )

    logger.error(
        message,
        extra={
            "user": user,
            "machine": machine,
            "invoice_number": invoice_number or "UNKNOWN",
        },
    )
