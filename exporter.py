from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backup_locator import BackupCandidate
from dedupe import sha256_file
from manifest import AttachmentRecord
from utils import safe_read_json

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:  # pragma: no cover - dependency is expected in the project venv
    EncryptedBackup = None


CATEGORY_OUTPUT_DIRS = {
    "video": "videos",
    "image": "images",
    "audio": "audio",
    "document": "pdfs",
    "chat": "chats",
    "database": "databases",
    "other": "other",
}


@dataclass
class ExportResult:
    attempted: bool
    exported_count: int
    duplicate_count: int
    failed_count: int
    resumed_count: int
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def export_records(
    *,
    candidate: BackupCandidate | None,
    records: list[AttachmentRecord],
    output_dir: Path,
    types: list[str],
    resume: bool,
    password: str | None,
) -> ExportResult:
    if candidate is None:
        return ExportResult(
            attempted=False,
            exported_count=0,
            duplicate_count=0,
            failed_count=0,
            resumed_count=0,
            notes=["No backup candidate was selected, so export could not proceed."],
        )
    if not records:
        return ExportResult(
            attempted=False,
            exported_count=0,
            duplicate_count=0,
            failed_count=0,
            resumed_count=0,
            notes=["No export candidate records were available."],
        )

    resume_source_index, resume_hash_index, existing_filename_index = (
        _load_resume_indexes(output_dir) if resume else ({}, {}, {})
    )
    pending_records = [record for record in records if record.attachment_category in types]

    exported_count = 0
    duplicate_count = 0
    failed_count = 0
    resumed_count = 0
    notes: list[str] = []

    for record in pending_records:
        source_key = _source_key(record)
        prior_record = resume_source_index.get(source_key)
        if prior_record and _resume_record(record, prior_record):
            resumed_count += 1
            continue
        if _resume_from_existing_output(record, existing_filename_index):
            resumed_count += 1
            if record.sha256 and record.exported_path:
                resume_hash_index.setdefault(record.sha256, record.exported_path)
            continue

    records_requiring_decryption = [
        record
        for record in pending_records
        if record.status not in {"exported", "duplicate"}
    ]

    if candidate.is_encrypted and records_requiring_decryption:
        if not password:
            return ExportResult(
                attempted=False,
                exported_count=0,
                duplicate_count=0,
                failed_count=0,
                resumed_count=resumed_count,
                notes=[
                    "Export requires the encrypted backup password because at least one attachment still needs to be decrypted."
                ],
            )
        if EncryptedBackup is None:
            return ExportResult(
                attempted=False,
                exported_count=0,
                duplicate_count=0,
                failed_count=0,
                resumed_count=resumed_count,
                notes=[
                    "Export requires iphone_backup_decrypt, but it is not available in this Python environment."
                ],
            )
        try:
            backup = EncryptedBackup(
                backup_directory=candidate.backup_path,
                passphrase=password,
            )
            backup.test_decryption()
        except Exception:
            return ExportResult(
                attempted=False,
                exported_count=0,
                duplicate_count=0,
                failed_count=0,
                resumed_count=resumed_count,
                notes=["Decryption failed during export."],
            )
    else:
        backup = None

    temp_dir = output_dir / ".state" / candidate.backup_id / "export_tmp"
    category_directories = {category: output_dir / dirname for category, dirname in CATEGORY_OUTPUT_DIRS.items()}
    for directory in category_directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    hash_index = dict(resume_hash_index)
    for record in records_requiring_decryption:
        target_dir = category_directories.get(record.attachment_category, category_directories["other"])
        temp_path = temp_dir / _temp_filename(record)
        try:
            if candidate.is_encrypted:
                assert backup is not None
                backup.extract_file(
                    relative_path=record.source_relative_path,
                    domain_like=record.source_domain,
                    output_filename=str(temp_path),
                )
            else:
                source_blob = Path(candidate.backup_path) / record.backup_file_id[:2] / record.backup_file_id
                if not source_blob.is_file():
                    raise FileNotFoundError(str(source_blob))
                shutil.copy2(source_blob, temp_path)

            record.decrypted_source_path = str(temp_path)
            record.file_size = temp_path.stat().st_size
            record.sha256 = sha256_file(temp_path)

            prior_exported_path = hash_index.get(record.sha256)
            if prior_exported_path and Path(prior_exported_path).is_file():
                record.exported_path = prior_exported_path
                record.status = "duplicate"
                record.notes = (
                    f"{record.notes} Duplicate content matched an existing export."
                ).strip()
                duplicate_count += 1
                temp_path.unlink(missing_ok=True)
                continue

            final_name = record.original_filename or _temp_filename(record)
            final_path = _unique_output_path(target_dir, final_name)
            shutil.move(str(temp_path), final_path)
            record.decrypted_source_path = ""
            record.exported_path = str(final_path)
            record.status = "exported"
            record.notes = (f"{record.notes} Exported successfully.").strip()
            exported_count += 1
            hash_index[record.sha256] = str(final_path)
        except Exception as exc:
            failed_count += 1
            record.status = "failed"
            record.notes = (f"{record.notes} Export failed: {exc}").strip()
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    if exported_count:
        used_directories = sorted(
            {
                str(category_directories.get(record.attachment_category, category_directories["other"]))
                for record in records
                if record.status == "exported"
            }
        )
        notes.append(f"Exported {exported_count} files into {', '.join(used_directories)}.")
    if duplicate_count:
        notes.append(f"Detected {duplicate_count} duplicate files by SHA-256.")
    if resumed_count:
        notes.append(f"Reused {resumed_count} existing export results from the previous manifest.")
    if failed_count:
        notes.append(f"{failed_count} files failed during export.")
    if not notes:
        notes.append("No files needed exporting in this run.")

    return ExportResult(
        attempted=True,
        exported_count=exported_count,
        duplicate_count=duplicate_count,
        failed_count=failed_count,
        resumed_count=resumed_count,
        notes=notes,
    )


def _load_resume_indexes(
    output_dir: Path,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, str], dict[tuple[str, str], list[str]]]:
    manifest_payload = safe_read_json(output_dir / "manifest.json") or {}
    records = manifest_payload.get("records", []) if isinstance(manifest_payload, dict) else []
    source_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    hash_index: dict[str, str] = {}
    filename_index = _existing_output_filename_index(output_dir)
    for record in records:
        if not isinstance(record, dict):
            continue
        source_key = (
            str(record.get("source_domain", "")),
            str(record.get("source_relative_path", "")),
            str(record.get("backup_file_id", "")),
        )
        source_index[source_key] = record
        sha256 = str(record.get("sha256", "") or "")
        exported_path = str(record.get("exported_path", "") or "")
        if sha256 and exported_path and Path(exported_path).is_file():
            hash_index[sha256] = exported_path
    return source_index, hash_index, filename_index


def _resume_record(record: AttachmentRecord, prior_record: dict[str, Any]) -> bool:
    prior_status = str(prior_record.get("status", "") or "")
    prior_exported_path = str(prior_record.get("exported_path", "") or "")
    if prior_status not in {"exported", "duplicate"}:
        return False
    if not prior_exported_path or not Path(prior_exported_path).is_file():
        return False
    record.exported_path = prior_exported_path
    record.sha256 = str(prior_record.get("sha256", "") or "")
    record.file_size = prior_record.get("file_size")
    record.status = prior_status
    record.notes = (
        f"{record.notes} Reused prior export result from the existing manifest."
    ).strip()
    return True


def _resume_from_existing_output(
    record: AttachmentRecord,
    existing_filename_index: dict[tuple[str, str], list[str]],
) -> bool:
    filename = record.original_filename or ""
    if not filename:
        return False
    candidates = existing_filename_index.get((record.attachment_category, filename), [])
    if len(candidates) != 1:
        return False
    path = Path(candidates[0])
    if not path.is_file():
        return False
    record.exported_path = str(path)
    record.file_size = path.stat().st_size
    record.sha256 = sha256_file(path)
    record.status = "exported"
    record.notes = (
        f"{record.notes} Reused an existing exported file found on disk."
    ).strip()
    return True


def _source_key(record: AttachmentRecord) -> tuple[str, str, str]:
    return (record.source_domain, record.source_relative_path, record.backup_file_id)


def _temp_filename(record: AttachmentRecord) -> str:
    suffix = Path(record.original_filename or record.source_relative_path).suffix
    suffix = suffix if suffix else ".bin"
    return f"{record.backup_file_id}{suffix}"


def _unique_output_path(directory: Path, filename: str) -> str:
    candidate = directory / filename
    if not candidate.exists():
        return str(candidate)
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = directory / f"{stem}-{counter}{suffix}"
        if not next_candidate.exists():
            return str(next_candidate)
        counter += 1


def _existing_output_filename_index(output_dir: Path) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for category, dirname in CATEGORY_OUTPUT_DIRS.items():
        subdir = output_dir / dirname
        if not subdir.is_dir():
            continue
        for path in subdir.iterdir():
            if not path.is_file():
                continue
            index.setdefault((category, path.name), []).append(str(path))
    return index
