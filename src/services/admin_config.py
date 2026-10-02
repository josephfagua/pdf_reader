"""admin_config.py — Administrator-controlled MDIP configuration.

The user-facing configuration stores only input/output folders.
Administrator settings store the audit-log destination and the location of the
admin configuration file itself.

A small machine-wide bootstrap pointer is kept under ProgramData so the
application does not need to hardcode the administrator configuration file
location. The pointer tells MDIP where the administrator configuration lives.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


PROGRAM_DATA_DIR = (
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    / "MD Invoice Processor"
)

# This is only the machine-wide bootstrap pointer. It is not the admin
# configuration itself. Its purpose is to tell MDIP where the admin config
# file has been placed by an administrator.
ADMIN_CONFIG_POINTER_FILE = PROGRAM_DATA_DIR / "admin_config_location.json"

# Default location used before an administrator has configured another path.
DEFAULT_ADMIN_CONFIG_FILE = PROGRAM_DATA_DIR / "admin_config.json"
DEFAULT_AUDIT_LOG_FILE = PROGRAM_DATA_DIR / "app_audit_log.log"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}

    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix="mdip_admin_",
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


def get_admin_config_path() -> Path:
    """Return the configured admin-config path, falling back to ProgramData."""
    pointer = _read_json(ADMIN_CONFIG_POINTER_FILE)
    configured = str(pointer.get("admin_config_path") or "").strip()

    if configured:
        path = Path(configured).expanduser()
        if path.exists() and path.is_dir():
            return path / "admin_config.json"
        if not path.suffix:
            return path.with_name(path.name + ".json")
        return path

    return DEFAULT_ADMIN_CONFIG_FILE


def load_admin_config() -> dict[str, Any]:
    """Load the administrator configuration from its configured location."""
    return _read_json(get_admin_config_path())


def get_audit_log_path(config: dict[str, Any] | None = None) -> str | None:
    """Return the configured shared audit-log path, if configured."""
    config = config if config is not None else load_admin_config()
    value = config.get("audit_log_path")

    if value is None:
        return None

    value = str(value).strip()
    return value or None


def save_admin_config(
    admin_config_path: str,
    audit_log_path: str,
) -> dict[str, Any]:
    """Create/update the admin configuration and its machine-wide pointer."""
    admin_path = Path(admin_config_path).expanduser()
    audit_path = Path(str(audit_log_path).strip()).expanduser()

    if not str(admin_path).strip():
        raise ValueError("An administrator configuration file path is required.")

    if not str(audit_path).strip():
        raise ValueError("An audit log path is required.")

    if admin_path.exists() and admin_path.is_dir():
        admin_path = admin_path / "admin_config.json"

    if audit_path.exists() and audit_path.is_dir():
        audit_path = audit_path / "app_audit_log.log"

    if admin_path.suffix.lower() != ".json":
        admin_path = admin_path.with_suffix(".json")

    if audit_path.suffix.lower() != ".log":
        audit_path = audit_path.with_suffix(".log")

    admin_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "admin_config_path": str(admin_path),
        "audit_log_path": str(audit_path),
    }

    # Write the actual admin configuration first. Only after it exists do we
    # update the bootstrap pointer to the new location.
    _write_json_atomic(admin_path, data)
    _write_json_atomic(
        ADMIN_CONFIG_POINTER_FILE,
        {"admin_config_path": str(admin_path)},
    )

    return data
