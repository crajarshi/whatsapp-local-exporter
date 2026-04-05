from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backup_locator import BackupCandidate
from backup_manifest_parser import BackupStructureEvidence
from manifest import AttachmentRecord
from whatsapp_locator import WhatsAppLocationEvidence

try:
    from iphone_backup_decrypt.utils import FilePlist
except ImportError:  # pragma: no cover - dependency is expected in the project venv
    FilePlist = None


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".rtf",
    ".epub",
    ".mht",
    ".pages",
    ".numbers",
    ".key",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp3", ".aac", ".wav", ".amr"}
SQLITE_SUFFIXES = (".sqlite", ".sqlite-shm", ".sqlite-wal")
CHAT_DATABASE_MARKERS = (
    "chatstorage.sqlite",
    "extchatdatabase.sqlite",
    "messaginginfradatabase.sqlite",
)


@dataclass
class AttachmentEnumerationResult:
    total_chats_discovered: int | None
    total_messages_discovered: int | None
    total_attachment_records_discovered: int | None
    total_video_records: int | None
    total_pdf_document_records: int | None
    total_records_with_resolvable_local_content: int | None
    total_metadata_only_records: int | None
    total_unresolved_records: int | None
    total_whatsapp_records: int | None = None
    total_whatsapp_file_records: int | None = None
    total_whatsapp_file_bytes: int | None = None
    total_media_file_records: int | None = None
    total_media_file_bytes: int | None = None
    total_export_candidate_bytes: int | None = None
    total_video_bytes: int | None = None
    total_pdf_document_bytes: int | None = None
    export_category_counts: dict[str, int] = field(default_factory=dict)
    export_category_bytes: dict[str, int] = field(default_factory=dict)
    target_chat_count: int | None = None
    records: list[AttachmentRecord] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["records"] = [asdict(record) for record in self.records]
        return payload


def enumerate_attachments(
    *,
    candidate: BackupCandidate | None,
    structure: BackupStructureEvidence,
    whatsapp: WhatsAppLocationEvidence,
    types: list[str],
    **_: Any,
) -> AttachmentEnumerationResult:
    if candidate is None:
        return _unresolved_result("No backup candidate was selected, so attachment enumeration could not proceed.")
    if not structure.manifest_db_accessible:
        return _unresolved_result(
            "Manifest.db is not yet accessible, so attachment enumeration cannot proceed."
        )

    manifest_db_path = structure.manifest_db_opened_path or structure.manifest_db_path
    if not manifest_db_path:
        return _unresolved_result(
            "No readable Manifest.db path was available for attachment enumeration."
        )

    whatsapp_domains = _discover_whatsapp_domains(manifest_db_path)
    if not whatsapp_domains:
        return _unresolved_result(
            "No WhatsApp domains were found in the decrypted manifest."
        )

    rows = _load_whatsapp_file_rows(manifest_db_path)
    if not rows:
        return _unresolved_result(
            "No WhatsApp file rows were found in the decrypted manifest."
        )

    total_whatsapp_records = whatsapp.candidate_row_count
    total_whatsapp_file_records = 0
    total_whatsapp_file_bytes = 0
    total_media_file_records = 0
    total_media_file_bytes = 0
    total_attachment_records_discovered = 0
    total_video_records = 0
    total_pdf_document_records = 0
    total_export_candidate_bytes = 0
    total_video_bytes = 0
    total_pdf_document_bytes = 0
    export_category_counts: dict[str, int] = {}
    export_category_bytes: dict[str, int] = {}
    resolvable_count = 0
    metadata_only_count = 0
    target_chat_ids: set[str] = set()
    records: list[AttachmentRecord] = []
    unresolved: list[dict[str, Any]] = []
    include_all_types = "all" in types

    for row in rows:
        file_id = str(row["fileID"])
        domain = str(row["domain"] or "")
        relative_path = str(row["relativePath"] or "")
        file_blob = row["file"]
        file_size = _extract_filesize(file_blob)
        timestamp = _extract_timestamp(file_blob)

        total_whatsapp_file_records += 1
        total_whatsapp_file_bytes += file_size or 0

        extension = _suffix(relative_path)
        media_category = _media_category_from_extension(extension)
        if media_category is not None:
            total_media_file_records += 1
            total_media_file_bytes += file_size or 0

        export_category = _export_category_from_path(relative_path)
        if not include_all_types and export_category not in types:
            continue

        total_attachment_records_discovered += 1
        total_export_candidate_bytes += file_size or 0
        export_category_counts[export_category] = export_category_counts.get(export_category, 0) + 1
        export_category_bytes[export_category] = export_category_bytes.get(export_category, 0) + (file_size or 0)
        if export_category == "video":
            total_video_records += 1
            total_video_bytes += file_size or 0
        if export_category == "document":
            total_pdf_document_records += 1
            total_pdf_document_bytes += file_size or 0

        chat_id = _derive_chat_id(relative_path)
        if chat_id:
            target_chat_ids.add(chat_id)

        blob_path = _backup_blob_path(candidate.backup_path, file_id)
        blob_exists = blob_path.is_file()
        if blob_exists:
            resolvable_count += 1
        else:
            metadata_only_count += 1
            unresolved.append(
                {
                    "status": "missing",
                    "notes": (
                        "The manifest points to an attachment candidate, but its encrypted backup blob is missing: "
                        f"{blob_path}"
                    ),
                }
            )

        records.append(
            AttachmentRecord(
                backup_id=candidate.backup_id,
                backup_path=candidate.backup_path,
                source_domain=domain,
                source_relative_path=relative_path,
                chat_id=chat_id,
                chat_name="",
                message_id="",
                sender="",
                timestamp=timestamp,
                attachment_category=export_category,
                mime_type=_mime_type_for_path(relative_path),
                original_filename=Path(relative_path).name,
                backup_file_id=file_id,
                decrypted_source_path="",
                exported_path="",
                file_size=file_size,
                sha256="",
                status="metadata_only" if blob_exists else "missing",
                notes=_record_notes(relative_path, chat_id, blob_exists),
            )
        )

    notes = [
        (
            "Pre-export planning is based on decrypted Manifest.db rows. Chat identifiers currently come from "
            "WhatsApp media paths when available, so this is a manifest-level export plan rather than a fully joined "
            "chat/message database view."
        ),
        (
            f"WhatsApp domains considered for export planning: {', '.join(whatsapp_domains)}"
        ),
    ]
    if include_all_types:
        notes.append(
            "The selected types include 'all', so every WhatsApp file row in the decrypted manifest is eligible for export, including databases and uncategorized files."
        )
    if export_category_counts.get("chat"):
        notes.append(
            "The 'chat' category currently exports raw WhatsApp chat-related SQLite files. It does not yet produce a human-readable chat transcript."
        )

    return AttachmentEnumerationResult(
        total_chats_discovered=len(target_chat_ids),
        total_messages_discovered=None,
        total_attachment_records_discovered=total_attachment_records_discovered,
        total_video_records=total_video_records,
        total_pdf_document_records=total_pdf_document_records,
        total_records_with_resolvable_local_content=resolvable_count,
        total_metadata_only_records=metadata_only_count,
        total_unresolved_records=len(unresolved),
        total_whatsapp_records=total_whatsapp_records,
        total_whatsapp_file_records=total_whatsapp_file_records,
        total_whatsapp_file_bytes=total_whatsapp_file_bytes,
        total_media_file_records=total_media_file_records,
        total_media_file_bytes=total_media_file_bytes,
        total_export_candidate_bytes=total_export_candidate_bytes,
        total_video_bytes=total_video_bytes,
        total_pdf_document_bytes=total_pdf_document_bytes,
        export_category_counts=export_category_counts,
        export_category_bytes=export_category_bytes,
        target_chat_count=len(target_chat_ids),
        records=records,
        unresolved=unresolved,
        notes=notes,
    )


def _discover_whatsapp_domains(manifest_db_path: str) -> list[str]:
    connection = sqlite3.connect(f"file:{manifest_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT domain
            FROM Files
            WHERE flags = 1
              AND (
                    lower(COALESCE(domain, '')) LIKE '%whatsapp%'
                 OR lower(COALESCE(relativePath, '')) LIKE '%whatsapp%'
                 OR domain LIKE 'AppDomainGroup-group.net.whatsapp.%shared'
              )
            GROUP BY domain
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows if row and row[0]]


def _load_whatsapp_file_rows(manifest_db_path: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{manifest_db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT fileID, domain, relativePath, flags, file
            FROM Files
            WHERE flags = 1
              AND (
                    lower(COALESCE(domain, '')) LIKE '%whatsapp%'
                 OR lower(COALESCE(relativePath, '')) LIKE '%whatsapp%'
                 OR domain LIKE 'AppDomainGroup-group.net.whatsapp.%shared'
              )
            ORDER BY domain, relativePath
            """
        ).fetchall()
    finally:
        connection.close()
    return rows


def _extract_filesize(file_blob: bytes | None) -> int | None:
    if not file_blob or FilePlist is None:
        return None
    try:
        return int(FilePlist(file_blob).filesize)
    except Exception:
        return None


def _extract_timestamp(file_blob: bytes | None) -> str:
    if not file_blob or FilePlist is None:
        return ""
    try:
        mtime = FilePlist(file_blob).mtime
    except Exception:
        return ""
    if not mtime:
        return ""
    try:
        return datetime.fromtimestamp(float(mtime), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _suffix(relative_path: str) -> str:
    return Path(relative_path).suffix.lower()


def _media_category_from_extension(extension: str) -> str | None:
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in DOCUMENT_EXTENSIONS:
        return "document"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return None


def _export_category_from_path(relative_path: str) -> str:
    path_lower = relative_path.lower()
    name_lower = Path(relative_path).name.lower()
    extension = _suffix(relative_path)
    media_category = _media_category_from_extension(extension)
    if media_category is not None:
        return "document" if media_category == "document" else media_category
    if any(marker in path_lower for marker in CHAT_DATABASE_MARKERS) and _has_sqlite_suffix(name_lower):
        return "chat"
    if _has_sqlite_suffix(name_lower):
        return "database"
    return "other"


def _has_sqlite_suffix(filename_lower: str) -> bool:
    return any(filename_lower.endswith(suffix) for suffix in SQLITE_SUFFIXES)


def _derive_chat_id(relative_path: str) -> str:
    parts = relative_path.split("/")
    if len(parts) >= 3 and parts[0] == "Message" and parts[1] == "Media":
        return parts[2]
    return ""


def _backup_blob_path(backup_path: str, file_id: str) -> Path:
    return Path(backup_path) / file_id[:2] / file_id


def _mime_type_for_path(relative_path: str) -> str:
    path_lower = relative_path.lower()
    name_lower = Path(relative_path).name.lower()
    extension = _suffix(relative_path)
    mapping = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".gif": "image/gif",
        ".opus": "audio/ogg",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".aac": "audio/aac",
        ".wav": "audio/wav",
        ".amr": "audio/amr",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".rtf": "application/rtf",
        ".epub": "application/epub+zip",
        ".mht": "message/rfc822",
    }
    if any(marker in path_lower for marker in CHAT_DATABASE_MARKERS) and _has_sqlite_suffix(name_lower):
        return "application/x-sqlite3"
    if _has_sqlite_suffix(name_lower):
        return "application/x-sqlite3"
    return mapping.get(extension, "")


def _record_notes(relative_path: str, chat_id: str, blob_exists: bool) -> str:
    notes: list[str] = []
    if chat_id:
        notes.append("Chat identifier was derived from the WhatsApp media path.")
    else:
        notes.append("No chat identifier could be derived from the manifest path alone.")
    if blob_exists:
        notes.append("The encrypted backup blob exists and is eligible for export.")
    else:
        notes.append("The encrypted backup blob is missing on disk.")
    if relative_path.startswith("gif/"):
        notes.append("This record came from WhatsApp's gif cache path rather than Message/Media.")
    return " ".join(notes)


def _unresolved_result(message: str) -> AttachmentEnumerationResult:
    return AttachmentEnumerationResult(
        total_chats_discovered=None,
        total_messages_discovered=None,
        total_attachment_records_discovered=None,
        total_video_records=None,
        total_pdf_document_records=None,
        total_records_with_resolvable_local_content=None,
        total_metadata_only_records=None,
        total_unresolved_records=1,
        records=[],
        unresolved=[{"status": "unresolved", "notes": message}],
        notes=[message],
    )
