from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any


DEFAULT_BACKUP_ROOT = (
    Path.home() / "Library" / "Application Support" / "MobileSync" / "Backup"
)
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "whatsapp-export"
BACKUP_PASSWORD_ENV_VAR = "FINDER_BACKUP_PASSWORD"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def ensure_output_dir(path: str | os.PathLike[str]) -> Path:
    output_dir = Path(path).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def prompt_password(enabled: bool) -> str | None:
    if enabled:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise RuntimeError(
                "A hidden password prompt requires an interactive TTY. Re-run with --password-prompt in a terminal session."
            )
        return getpass("Encrypted backup password: ")
    return os.environ.get(BACKUP_PASSWORD_ENV_VAR) or None


def bool_to_text(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def format_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{value} B"
