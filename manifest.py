from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from utils import format_bytes, now_iso, write_json


@dataclass
class AttachmentRecord:
    backup_id: str
    backup_path: str
    source_domain: str
    source_relative_path: str
    chat_id: str
    chat_name: str
    message_id: str
    sender: str
    timestamp: str
    attachment_category: str
    mime_type: str
    original_filename: str
    backup_file_id: str
    decrypted_source_path: str
    exported_path: str
    file_size: int | None
    sha256: str
    status: str
    notes: str


@dataclass
class DryRunSummary:
    generated_at: str
    selected_backup_path: str
    backup_id: str
    backup_encrypted: bool | None
    decryption_succeeded: bool | None
    whatsapp_data_located: bool | None
    total_whatsapp_records: int | None
    total_whatsapp_file_records: int | None
    total_whatsapp_file_bytes: int | None
    total_media_file_records: int | None
    total_media_file_bytes: int | None
    total_chats_discovered: int | None
    total_messages_discovered: int | None
    total_attachment_records_discovered: int | None
    total_export_candidate_bytes: int | None
    total_video_records: int | None
    total_video_bytes: int | None
    total_pdf_document_records: int | None
    total_pdf_document_bytes: int | None
    total_records_with_resolvable_local_content: int | None
    total_metadata_only_records: int | None
    total_unresolved_records: int | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_artifacts(
    output_dir: Path,
    summary: DryRunSummary,
    records: list[AttachmentRecord],
    unresolved: list[dict[str, Any]],
    investigation_payload: dict[str, Any],
) -> None:
    manifest_path = output_dir / "manifest.json"
    unresolved_path = output_dir / "unresolved.json"
    summary_path = output_dir / "summary.txt"

    manifest_payload = {
        "generated_at": now_iso(),
        "summary": summary.to_dict(),
        "investigation": investigation_payload,
        "records": [asdict(record) for record in records],
    }
    write_json(manifest_path, manifest_payload)
    write_json(unresolved_path, unresolved)

    lines = [
        f"Generated at: {summary.generated_at}",
        f"Selected backup path: {summary.selected_backup_path or 'unknown'}",
        f"Backup ID: {summary.backup_id or 'unknown'}",
        f"Backup encrypted: {_bool_fmt(summary.backup_encrypted)}",
        f"Decryption succeeded: {_bool_fmt(summary.decryption_succeeded)}",
        f"WhatsApp data located: {_bool_fmt(summary.whatsapp_data_located)}",
        f"Total WhatsApp rows: {_fmt(summary.total_whatsapp_records)}",
        f"Total WhatsApp file records: {_fmt(summary.total_whatsapp_file_records)}",
        f"Total WhatsApp file bytes: {_fmt_bytes(summary.total_whatsapp_file_bytes)}",
        f"Total media file records: {_fmt(summary.total_media_file_records)}",
        f"Total media file bytes: {_fmt_bytes(summary.total_media_file_bytes)}",
        f"Total chats discovered: {_fmt(summary.total_chats_discovered)}",
        f"Total messages discovered: {_fmt(summary.total_messages_discovered)}",
        f"Total attachment records discovered: {_fmt(summary.total_attachment_records_discovered)}",
        f"Total export candidate bytes: {_fmt_bytes(summary.total_export_candidate_bytes)}",
        f"Total video records: {_fmt(summary.total_video_records)}",
        f"Total video bytes: {_fmt_bytes(summary.total_video_bytes)}",
        f"Total pdf/document records: {_fmt(summary.total_pdf_document_records)}",
        f"Total pdf/document bytes: {_fmt_bytes(summary.total_pdf_document_bytes)}",
        (
            "Total records with resolvable local content: "
            f"{_fmt(summary.total_records_with_resolvable_local_content)}"
        ),
        f"Total metadata-only records: {_fmt(summary.total_metadata_only_records)}",
        f"Total unresolved records: {_fmt(summary.total_unresolved_records)}",
        "",
        "Notes:",
    ]
    lines.extend(f"- {note}" for note in summary.notes)
    summary_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _fmt(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _bool_fmt(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value} ({format_bytes(value)})"
