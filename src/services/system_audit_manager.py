"""system_audit_manager.py — System-wide latest invoice tracking for MDIP."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

LATEST_FILENAME = "latest_invoices.json"
LOCK_DIR_NAME = ".latest_invoices.lock"
LOCK_TIMEOUT_SECONDS = 60
LOCK_WAIT_SECONDS = 0.1


def latest_state_path(audit_log_path: str | None) -> Path | None:
    """Return the system-wide latest-invoice state path next to the audit log."""
    if not audit_log_path or not str(audit_log_path).strip():
        return None
    return Path(str(audit_log_path)).expanduser().resolve().parent / LATEST_FILENAME


def _invoice_numeric_value(invoice_number: str | None) -> int | None:
    """Return the numeric portion used to determine the newest invoice."""
    if not invoice_number:
        return None

    match = re.search(r"\d+", str(invoice_number))
    if not match:
        return None

    try:
        return int(match.group(0))
    except ValueError:
        return None


def _timestamp_or_now(value: str | None) -> str:
    return value or datetime.now().astimezone().isoformat(timespec="seconds")


def _acquire_lock(lock_dir: Path) -> None:
    """Acquire a cross-process lock using an atomic directory creation."""
    start = time.monotonic()

    while True:
        try:
            lock_dir.mkdir(parents=True, exist_ok=False)
            return
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
                if age > LOCK_TIMEOUT_SECONDS:
                    lock_dir.rmdir()
                    continue
            except OSError:
                pass

            if time.monotonic() - start >= LOCK_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"Timed out waiting for system audit lock: {lock_dir}"
                )

            time.sleep(LOCK_WAIT_SECONDS)


def _release_lock(lock_dir: Path) -> None:
    try:
        lock_dir.rmdir()
    except OSError:
        pass


def _read_state(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}

    return data if isinstance(data, dict) else {}


def _write_state_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix="mdip_latest_",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def update_latest_invoices(
    audit_log_path: str | None,
    invoices: list[dict[str, Any]],
    *,
    batch_id: str,
    timestamp: str | None,
    user: str,
    machine: str,
) -> dict[str, Any]:
    """Update the system-wide latest invoice per client/location.

    The update occurs only after a complete successful batch. For each client,
    the invoice with the highest numeric invoice value is retained.
    """
    path = latest_state_path(audit_log_path)
    if path is None:
        return {}

    lock_dir = path.parent / LOCK_DIR_NAME
    _acquire_lock(lock_dir)

    try:
        state = _read_state(path)
        latest = state.get("latest", {})
        if not isinstance(latest, dict):
            latest = {}

        processed_at = _timestamp_or_now(timestamp)

        for invoice in invoices:
            client = str(invoice.get("client") or "").strip()
            invoice_number = str(invoice.get("invoice_number") or "").strip()
            numeric_value = _invoice_numeric_value(invoice_number)

            if not client or not invoice_number or numeric_value is None:
                continue

            previous = latest.get(client)
            previous_numeric = (
                previous.get("numeric_invoice_value")
                if isinstance(previous, dict)
                else None
            )

            try:
                previous_numeric = (
                    int(previous_numeric) if previous_numeric is not None else None
                )
            except (TypeError, ValueError):
                previous_numeric = None

            if previous_numeric is not None and numeric_value < previous_numeric:
                continue

            latest[client] = {
                "invoice_number": invoice_number,
                "numeric_invoice_value": numeric_value,
                "delivery_date": invoice.get("delivery_date"),
                "batch_id": batch_id,
                "processed_at": processed_at,
                "user": user,
                "machine": machine,
                "output_file": invoice.get("output_file"),
            }

        new_state = {
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "latest": latest,
        }
        _write_state_atomic(path, new_state)
        return latest
    finally:
        _release_lock(lock_dir)


def get_latest_invoices(audit_log_path: str | None) -> dict[str, Any]:
    """Return the system-wide latest-invoice map."""
    path = latest_state_path(audit_log_path)
    if path is None:
        return {}
    return _read_state(path).get("latest", {}) or {}
