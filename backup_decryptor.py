from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backup_locator import BackupCandidate

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    EncryptedBackup = None


@dataclass
class DecryptionAssessment:
    backup_id: str
    backup_encrypted: bool | None
    password_supplied: bool
    decryption_succeeded: bool | None
    method: str
    decrypted_manifest_db_path: str | None = None
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_decryption(
    candidate: BackupCandidate | None,
    password: str | None,
    output_dir: Path | None = None,
) -> DecryptionAssessment:
    if candidate is None:
        return DecryptionAssessment(
            backup_id="",
            backup_encrypted=None,
            password_supplied=bool(password),
            decryption_succeeded=None,
            method="not_attempted",
            notes=["No backup candidate was selected, so decryption could not be assessed."],
        )

    notes: list[str] = []
    if candidate.is_encrypted is True:
        notes.append("Manifest.plist indicates that this backup is encrypted.")
        if not password:
            existing_decrypted_manifest = _existing_decrypted_manifest_path(candidate, output_dir)
            if existing_decrypted_manifest:
                notes.append(
                    "Reusing a previously decrypted Manifest.db working copy from the local state directory."
                )
                return DecryptionAssessment(
                    backup_id=candidate.backup_id,
                    backup_encrypted=True,
                    password_supplied=False,
                    decryption_succeeded=True,
                    method="existing_decrypted_manifest",
                    decrypted_manifest_db_path=str(existing_decrypted_manifest),
                    notes=notes,
                )
            notes.append(
                "No password was supplied, so encrypted file decryption was not attempted."
            )
            return DecryptionAssessment(
                backup_id=candidate.backup_id,
                backup_encrypted=True,
                password_supplied=False,
                decryption_succeeded=False,
                method="password_required",
                notes=notes,
            )
        if EncryptedBackup is None:
            notes.append(
                "Encrypted backup decryption requires the optional iphone_backup_decrypt dependency, which is not installed in this Python environment."
            )
            return DecryptionAssessment(
                backup_id=candidate.backup_id,
                backup_encrypted=True,
                password_supplied=True,
                decryption_succeeded=False,
                method="dependency_missing",
                notes=notes,
            )
        if not candidate.manifest_db_path:
            notes.append("Manifest.db is missing, so the encrypted manifest cannot be decrypted.")
            return DecryptionAssessment(
                backup_id=candidate.backup_id,
                backup_encrypted=True,
                password_supplied=True,
                decryption_succeeded=False,
                method="manifest_missing",
                notes=notes,
            )
        try:
            backup = EncryptedBackup(
                backup_directory=candidate.backup_path,
                passphrase=password,
            )
            backup.test_decryption()
            decrypted_manifest_db_path = None
            if output_dir is not None:
                state_dir = output_dir / ".state" / candidate.backup_id
                state_dir.mkdir(parents=True, exist_ok=True)
                decrypted_manifest_path = state_dir / "Manifest.decrypted.db"
                backup.save_manifest_file(str(decrypted_manifest_path))
                decrypted_manifest_db_path = str(decrypted_manifest_path)
                notes.append(
                    "Manifest.db was decrypted to a working copy outside the original backup for read-only schema inspection."
                )
            notes.append("Encrypted backup decryption succeeded with the supplied password.")
            return DecryptionAssessment(
                backup_id=candidate.backup_id,
                backup_encrypted=True,
                password_supplied=True,
                decryption_succeeded=True,
                method="iphone_backup_decrypt",
                decrypted_manifest_db_path=decrypted_manifest_db_path,
                notes=notes,
            )
        except Exception as exc:  # pragma: no cover - depends on live password/backup state
            notes.append(
                "Encrypted backup decryption failed before a decrypted Manifest.db copy could be produced."
            )
            return DecryptionAssessment(
                backup_id=candidate.backup_id,
                backup_encrypted=True,
                password_supplied=True,
                decryption_succeeded=False,
                method="iphone_backup_decrypt",
                error=str(exc),
                notes=notes,
            )

    if candidate.is_encrypted is False:
        notes.append("Manifest.plist indicates that this backup is not encrypted.")
        return DecryptionAssessment(
            backup_id=candidate.backup_id,
            backup_encrypted=False,
            password_supplied=bool(password),
            decryption_succeeded=True,
            method="none_required",
            decrypted_manifest_db_path=candidate.manifest_db_path,
            notes=notes,
        )

    notes.append(
        "The backup encryption state could not be determined yet from the accessible metadata."
    )
    return DecryptionAssessment(
        backup_id=candidate.backup_id,
        backup_encrypted=None,
        password_supplied=bool(password),
        decryption_succeeded=None,
        method="unknown",
        notes=notes,
    )


def _existing_decrypted_manifest_path(
    candidate: BackupCandidate,
    output_dir: Path | None,
) -> Path | None:
    if output_dir is None:
        return None
    path = output_dir / ".state" / candidate.backup_id / "Manifest.decrypted.db"
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(str(path))
        try:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='Files'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return path if row else None
