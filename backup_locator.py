from __future__ import annotations

import plistlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from utils import DEFAULT_BACKUP_ROOT


@dataclass
class BackupCandidate:
    backup_id: str
    backup_path: str
    info_plist_path: str | None
    manifest_plist_path: str | None
    manifest_db_path: str | None
    status_plist_path: str | None
    is_encrypted: bool | None
    device_name: str | None
    display_name: str | None
    product_name: str | None
    product_version: str | None
    ios_version: str | None
    last_backup_date: str | None
    application_count: int | None
    manifest_top_level_keys: list[str] = field(default_factory=list)
    whatsapp_application_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw_metadata_keys: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackupDiscoveryResult:
    requested_root: str
    accessible: bool
    error: str
    candidates: list[BackupCandidate]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_root": self.requested_root,
            "accessible": self.accessible,
            "error": self.error,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def discover_backups(backup_path: str | None = None) -> BackupDiscoveryResult:
    if backup_path:
        candidate_path = Path(backup_path).expanduser().resolve()
        if not candidate_path.exists():
            return BackupDiscoveryResult(
                requested_root=str(candidate_path),
                accessible=False,
                error="Selected backup path does not exist.",
                candidates=[],
            )
        if candidate_path.is_dir() and _looks_like_backup_dir(candidate_path):
            return BackupDiscoveryResult(
                requested_root=str(candidate_path),
                accessible=True,
                error="",
                candidates=[_inspect_backup_dir(candidate_path)],
            )
        return BackupDiscoveryResult(
            requested_root=str(candidate_path),
            accessible=False,
            error="Selected path is not a recognizable Finder backup directory.",
            candidates=[],
        )

    root = DEFAULT_BACKUP_ROOT
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return BackupDiscoveryResult(
            requested_root=str(root),
            accessible=False,
            error="Default Finder backup root does not exist on this Mac.",
            candidates=[],
        )
    except PermissionError as exc:
        return BackupDiscoveryResult(
            requested_root=str(root),
            accessible=False,
            error=str(exc),
            candidates=[],
        )

    candidates = [
        _inspect_backup_dir(path)
        for path in sorted(entries)
        if path.is_dir() and _looks_like_backup_dir(path)
    ]
    return BackupDiscoveryResult(
        requested_root=str(root),
        accessible=True,
        error="",
        candidates=candidates,
    )


def _looks_like_backup_dir(path: Path) -> bool:
    try:
        names = {item.name for item in path.iterdir()}
    except PermissionError:
        return True
    return bool({"Info.plist", "Manifest.plist", "Status.plist", "Manifest.db"} & names)


def _inspect_backup_dir(path: Path) -> BackupCandidate:
    info_path = _optional_path(path / "Info.plist")
    manifest_plist_path = _optional_path(path / "Manifest.plist")
    manifest_db_path = _optional_path(path / "Manifest.db")
    status_path = _optional_path(path / "Status.plist")

    info_plist = _read_plist(path / "Info.plist")
    manifest_plist = _read_plist(path / "Manifest.plist")
    status_plist = _read_plist(path / "Status.plist")

    raw_metadata_keys = {
        "Info.plist": sorted(info_plist.keys()) if info_plist else [],
        "Manifest.plist": sorted(manifest_plist.keys()) if manifest_plist else [],
        "Status.plist": sorted(status_plist.keys()) if status_plist else [],
    }

    encrypted_value = None
    if manifest_plist and isinstance(manifest_plist.get("IsEncrypted"), bool):
        encrypted_value = manifest_plist["IsEncrypted"]
    applications = manifest_plist.get("Applications") if manifest_plist else None
    application_count = len(applications) if isinstance(applications, dict) else None
    whatsapp_application_ids = (
        sorted(
            key
            for key, value in applications.items()
            if "whatsapp" in key.lower() or "whatsapp" in str(value).lower()
        )
        if isinstance(applications, dict)
        else []
    )

    notes: list[str] = []
    if manifest_plist is None and manifest_plist_path:
        notes.append("Manifest.plist exists but could not be parsed.")
    if info_plist is None and info_path:
        notes.append("Info.plist exists but could not be parsed.")
    if manifest_db_path and not manifest_plist_path:
        notes.append("Manifest.db present even though Manifest.plist is missing.")
    if encrypted_value is True:
        notes.append("Manifest.plist indicates that this Finder backup is encrypted.")
    if whatsapp_application_ids:
        notes.append(
            "Manifest.plist lists WhatsApp-related application and app-group identifiers."
        )

    lockdown = manifest_plist.get("Lockdown") if isinstance(manifest_plist, dict) else None

    return BackupCandidate(
        backup_id=path.name,
        backup_path=str(path),
        info_plist_path=info_path,
        manifest_plist_path=manifest_plist_path,
        manifest_db_path=manifest_db_path,
        status_plist_path=status_path,
        is_encrypted=encrypted_value,
        device_name=_string_value(info_plist, "Device Name") or _string_value(lockdown, "DeviceName"),
        display_name=_string_value(info_plist, "Display Name") or _string_value(lockdown, "DeviceName"),
        product_name=_string_value(info_plist, "Product Name") or _string_value(lockdown, "ProductType"),
        product_version=_string_value(info_plist, "Product Version") or _string_value(lockdown, "ProductVersion"),
        ios_version=_string_value(info_plist, "Product Version") or _string_value(lockdown, "ProductVersion"),
        last_backup_date=_string_value(info_plist, "Last Backup Date"),
        application_count=application_count,
        manifest_top_level_keys=sorted(manifest_plist.keys()) if manifest_plist else [],
        whatsapp_application_ids=whatsapp_application_ids,
        notes=notes,
        raw_metadata_keys=raw_metadata_keys,
    )


def _read_plist(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, PermissionError):
        return None
    return payload if isinstance(payload, dict) else None


def _optional_path(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _string_value(payload: dict[str, Any] | None, key: str) -> str | None:
    if not payload:
        return None
    value = payload.get(key)
    return str(value) if value is not None else None
