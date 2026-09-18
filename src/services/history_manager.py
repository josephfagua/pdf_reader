"""
history_manager.py — Recent successful processing history for MDIP.

Phase 3 foundation:
- Keeps the two most recent successfully completed batches.
- Stores history locally under LOCALAPPDATA MD Invoice Processor.
- Uses invoice number as the duplicate-detection key.
- History storage failures never prevent a batch from completing.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

APP_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "MD Invoice Processor"
HISTORY_FILE = APP_DIR / "history.json"
MAX_BATCHES = 2


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _create_batch_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:4].upper()
    return f"BATCH-{timestamp}-{suffix}"


class HistoryManager:
    """Manage MDIP's two most recent successful processing batches."""

    def __init__(self, history_file: Path | None = None, max_batches: int = MAX_BATCHES):
        self.history_file = history_file or HISTORY_FILE
        self.max_batches = max_batches
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def get_recent_batches(self) -> list[dict[str, Any]]:
        """Return recent batches newest-first. Corrupt history becomes empty history."""
        try:
            with self.history_file.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError, TypeError):
            return []

        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)][:self.max_batches]

    def record_batch(
        self,
        user: str,
        machine: str,
        invoices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Record one fully successful batch and retain only the two newest batches."""
        batch = {
            "batch_id": _create_batch_id(),
            "timestamp": _now_iso(),
            "user": user,
            "machine": machine,
            "invoice_count": len(invoices),
            "clients": self._unique_clients(invoices),
            "invoices": invoices,
        }
        self._write_history([batch, *self.get_recent_batches()][:self.max_batches])
        return batch

    def find_duplicate_invoices(
        self,
        invoice_numbers: list[str | None],
    ) -> list[dict[str, Any]]:
        """Find selected invoice numbers in either of the two recent batches."""
        numbers = {
            str(number).strip().upper()
            for number in invoice_numbers
            if number and str(number).strip()
            and str(number).strip().upper() != "UNKNOWN"
        }
        if not numbers:
            return []

        matches: list[dict[str, Any]] = []
        for batch in self.get_recent_batches():
            for invoice in batch.get("invoices", []):
                number = str(invoice.get("invoice_number") or "").strip().upper()
                if number in numbers:
                    matches.append({
                        "invoice_number": number,
                        "batch_id": batch.get("batch_id", "UNKNOWN"),
                        "timestamp": batch.get("timestamp"),
                        "user": batch.get("user", "UNKNOWN"),
                        "machine": batch.get("machine", "UNKNOWN"),
                        "client": invoice.get("client"),
                        "output_file": invoice.get("output_file"),
                    })
        return matches

    @staticmethod
    def _unique_clients(invoices: list[dict[str, Any]]) -> list[str]:
        clients: list[str] = []
        seen: set[str] = set()
        for invoice in invoices:
            client = str(invoice.get("client") or "").strip()
            if client and client not in seen:
                clients.append(client)
                seen.add(client)
        return clients

    def _write_history(self, batches: list[dict[str, Any]]) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="mdip_history_",
            suffix=".tmp",
            dir=self.history_file.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(batches, file, indent=2, ensure_ascii=False)
            os.replace(temp_name, self.history_file)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


history_manager = HistoryManager()
