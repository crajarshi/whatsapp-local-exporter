from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from whatsapp_local_exporter.discovery import KNOWN_ROOTS, StorageScan


APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
PDF_SUFFIXES = {".pdf"}


@dataclass
class ManifestRecord:
    chat_id: str
    chat_name: str
    message_id: str
    sender: str
    timestamp: str
    attachment_type: str
    mime_type: str
    original_filename: str
    source_local_path: str
    exported_path: str
    file_size: int | None
    sha256: str
    status: str
    notes: str
    record_key: str

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("record_key", None)
        return data


@dataclass
class ExportSummary:
    generated_at: str
    run_mode: str
    selected_types: list[str]
    output_dir: str
    primary_database: str | None
    total_messages: int
    total_chats: int
    total_media_rows: int
    target_records: int
    exported_records: int
    duplicate_records: int
    dry_run_records: int
    unresolved_records: int
    unique_exported_files: int
    total_exported_bytes: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExporterError(RuntimeError):
    pass


class WhatsAppLocalExporter:
    def __init__(self, scan: StorageScan, output_dir: Path, verbose: bool = False) -> None:
        self.scan = scan
        self.output_dir = output_dir
        self.verbose = verbose
        self.manifest_path = self.output_dir / "manifest.json"
        self.summary_path = self.output_dir / "summary.txt"
        self.unresolved_path = self.output_dir / "unresolved.json"
        self.video_output_dir = self.output_dir / "videos"
        self.pdf_output_dir = self.output_dir / "pdfs"

    def run(
        self,
        selected_types: list[str],
        do_export: bool,
        dry_run: bool,
        resume: bool,
    ) -> tuple[ExportSummary, list[ManifestRecord]]:
        if not self.scan.primary_database:
            raise ExporterError("No WhatsApp database was discovered.")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if do_export:
            self.video_output_dir.mkdir(parents=True, exist_ok=True)
            self.pdf_output_dir.mkdir(parents=True, exist_ok=True)

        stats = self._get_database_stats()
        manifest_records = self._enumerate_target_records(selected_types)

        previous_records = self._load_existing_manifest() if resume else {}
        hash_to_export: dict[str, str] = {}
        for record in previous_records.values():
            if record.sha256 and record.exported_path:
                hash_to_export[record.sha256] = record.exported_path

        processed_records: list[ManifestRecord] = []
        exported_bytes = 0

        for record in manifest_records:
            if resume and record.record_key in previous_records:
                processed_records.append(previous_records[record.record_key])
                continue

            if record.status == "missing_source":
                processed_records.append(record)
                continue

            if dry_run and not do_export:
                record.status = "dry-run"
                processed_records.append(record)
                continue

            try:
                sha256_value = _sha256_file(Path(record.source_local_path))
                record.sha256 = sha256_value
                destination = hash_to_export.get(sha256_value)
                if destination:
                    record.exported_path = destination
                    record.status = "duplicate"
                    note = "duplicate content; reused existing exported file"
                    record.notes = _append_note(record.notes, note)
                else:
                    target_dir = (
                        self.video_output_dir
                        if record.attachment_type == "video"
                        else self.pdf_output_dir
                    )
                    suffix = Path(record.source_local_path).suffix.lower()
                    canonical_path = target_dir / f"{sha256_value}{suffix}"
                    if do_export:
                        shutil.copy2(record.source_local_path, canonical_path)
                        exported_bytes += canonical_path.stat().st_size
                    record.exported_path = str(canonical_path)
                    record.status = "exported" if do_export else "dry-run"
                    hash_to_export[sha256_value] = str(canonical_path)
            except OSError as exc:
                record.status = "export_error"
                record.notes = _append_note(record.notes, str(exc))

            processed_records.append(record)

        unresolved = [
            record.to_public_dict()
            for record in processed_records
            if record.status not in {"exported", "duplicate", "dry-run"}
        ]

        summary = ExportSummary(
            generated_at=_now_iso(),
            run_mode="export" if do_export else "dry-run",
            selected_types=selected_types,
            output_dir=str(self.output_dir),
            primary_database=self.scan.primary_database,
            total_messages=stats["total_messages"],
            total_chats=stats["total_chats"],
            total_media_rows=stats["total_media_rows"],
            target_records=len(processed_records),
            exported_records=sum(1 for record in processed_records if record.status == "exported"),
            duplicate_records=sum(1 for record in processed_records if record.status == "duplicate"),
            dry_run_records=sum(1 for record in processed_records if record.status == "dry-run"),
            unresolved_records=len(unresolved),
            unique_exported_files=len(hash_to_export),
            total_exported_bytes=exported_bytes,
            notes=self._build_summary_notes(),
        )

        self._write_outputs(summary=summary, records=processed_records, unresolved=unresolved)
        return summary, processed_records

    def _build_summary_notes(self) -> list[str]:
        notes = [
            "Primary extraction uses ChatStorage.sqlite directly.",
            "The database stores relative media paths such as Media/...; this exporter resolves them against discovered WhatsApp roots, including Message/Media/... on newer native builds.",
            "original_filename is best-effort because WhatsApp does not expose a stable filename column in the observed schema.",
        ]
        if (
            KNOWN_ROOTS["modern_group_shared"] / "ExtChatDB" / "ExtChatDatabase.sqlite"
        ).exists():
            notes.append(
                "ExtChatDatabase.sqlite was discovered but may not contain active media rows on every installation, so it is not required for export."
            )
        return notes

    def _enumerate_target_records(self, selected_types: list[str]) -> list[ManifestRecord]:
        database = self.scan.primary_database
        if not database:
            raise ExporterError("No primary database available.")

        query = """
            SELECT
                m.Z_PK AS message_pk,
                COALESCE(NULLIF(m.ZSTANZAID, ''), CAST(m.Z_PK AS TEXT)) AS stable_message_id,
                m.ZMESSAGEDATE AS message_date,
                m.ZISFROMME AS is_from_me,
                m.ZFROMJID AS from_jid,
                m.ZTOJID AS to_jid,
                gm.ZMEMBERJID AS group_member_jid,
                gm.ZCONTACTNAME AS group_contact_name,
                gm.ZFIRSTNAME AS group_first_name,
                cs.ZCONTACTJID AS chat_id,
                cs.ZPARTNERNAME AS chat_name,
                mi.ZMEDIALOCALPATH AS media_local_path,
                mi.ZFILESIZE AS file_size,
                mi.ZTITLE AS media_title,
                mi.ZMEDIAURL AS media_url
            FROM ZWAMESSAGE m
            JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM
            LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
            LEFT JOIN ZWACHATSESSION cs ON cs.Z_PK = m.ZCHATSESSION
            WHERE mi.ZMEDIALOCALPATH IS NOT NULL
              AND mi.ZMEDIALOCALPATH != ''
            ORDER BY m.ZMESSAGEDATE ASC, m.Z_PK ASC
        """

        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row

        records: list[ManifestRecord] = []
        with connection:
            for row in connection.execute(query):
                classification = _classify_attachment(
                    media_local_path=row["media_local_path"],
                    media_url=row["media_url"],
                )
                if classification is None or classification not in selected_types:
                    continue

                resolved_path, path_notes = self._resolve_media_path(row["media_local_path"])
                if resolved_path is None:
                    record = self._build_record(
                        row=row,
                        attachment_type=classification,
                        resolved_path=row["media_local_path"],
                        extra_notes=_append_note(
                            path_notes,
                            "stored relative path could not be resolved to a local file",
                        ),
                    )
                    record.status = "missing_source"
                    records.append(record)
                    continue

                record = self._build_record(
                    row=row,
                    attachment_type=classification,
                    resolved_path=str(resolved_path),
                    extra_notes=path_notes,
                )
                records.append(record)

        return records

    def _build_record(
        self,
        row: sqlite3.Row,
        attachment_type: str,
        resolved_path: str,
        extra_notes: str,
    ) -> ManifestRecord:
        chat_id = row["chat_id"] or _chat_id_from_relative_path(row["media_local_path"])
        chat_name = row["chat_name"] or chat_id or "unknown-chat"
        sender = _derive_sender(
            is_from_me=bool(row["is_from_me"]),
            from_jid=row["from_jid"],
            to_jid=row["to_jid"],
            group_member_jid=row["group_member_jid"],
            group_contact_name=row["group_contact_name"],
            group_first_name=row["group_first_name"],
            chat_id=chat_id,
        )
        mime_type = _infer_mime_type(resolved_path)
        original_filename, filename_notes = _infer_original_filename(
            media_title=row["media_title"],
            media_local_path=row["media_local_path"],
        )
        notes = _merge_notes(extra_notes, filename_notes)
        return ManifestRecord(
            chat_id=chat_id or "unknown-chat",
            chat_name=chat_name,
            message_id=str(row["stable_message_id"]),
            sender=sender,
            timestamp=_apple_timestamp_to_iso(row["message_date"]),
            attachment_type=attachment_type,
            mime_type=mime_type,
            original_filename=original_filename,
            source_local_path=str(resolved_path),
            exported_path="",
            file_size=int(row["file_size"]) if row["file_size"] is not None else None,
            sha256="",
            status="pending",
            notes=notes,
            record_key=f"{chat_id}|{row['message_pk']}|{row['media_local_path']}",
        )

    def _resolve_media_path(self, relative_path: str) -> tuple[Path | None, str]:
        source = Path(relative_path)
        if source.is_absolute():
            return (source, "") if source.exists() else (None, "absolute source path does not exist")

        bases = [
            KNOWN_ROOTS["modern_group_shared"],
            KNOWN_ROOTS["modern_group_shared"] / "Message",
            KNOWN_ROOTS["legacy_group_shared"],
            KNOWN_ROOTS["legacy_group_shared"] / "Message",
            KNOWN_ROOTS["modern_container"] / "Data",
            KNOWN_ROOTS["modern_container"] / "Data" / "Documents",
            KNOWN_ROOTS["legacy_container"] / "Data",
            KNOWN_ROOTS["legacy_container"] / "Data" / "Documents",
        ]

        attempts = [base / source for base in bases if base.exists()]
        if relative_path.startswith("Media/"):
            remapped = Path("Message") / source
            attempts.extend(base.parent / remapped for base in bases if base.exists() and base.name == "Message")
            attempts.extend((KNOWN_ROOTS["modern_group_shared"] / remapped, KNOWN_ROOTS["legacy_group_shared"] / remapped))

        seen: set[str] = set()
        for attempt in attempts:
            key = str(attempt)
            if key in seen:
                continue
            seen.add(key)
            if attempt.exists():
                note = ""
                if "Message/Media" in key and relative_path.startswith("Media/"):
                    note = "resolved database Media/... path through Message/Media/... on disk"
                return attempt, note

        return None, "no local file matched the stored relative path"

    def _get_database_stats(self) -> dict[str, int]:
        database = self.scan.primary_database
        if not database:
            raise ExporterError("No primary database available.")

        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        with connection:
            total_messages = connection.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0]
            total_chats = connection.execute("SELECT COUNT(*) FROM ZWACHATSESSION").fetchone()[0]
            total_media_rows = connection.execute("SELECT COUNT(*) FROM ZWAMEDIAITEM").fetchone()[0]
        return {
            "total_messages": int(total_messages),
            "total_chats": int(total_chats),
            "total_media_rows": int(total_media_rows),
        }

    def _load_existing_manifest(self) -> dict[str, ManifestRecord]:
        if not self.manifest_path.exists():
            return {}
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload.get("records", payload if isinstance(payload, list) else [])
        loaded: dict[str, ManifestRecord] = {}
        for item in records:
            record = ManifestRecord(
                chat_id=item.get("chat_id", ""),
                chat_name=item.get("chat_name", ""),
                message_id=item.get("message_id", ""),
                sender=item.get("sender", ""),
                timestamp=item.get("timestamp", ""),
                attachment_type=item.get("attachment_type", ""),
                mime_type=item.get("mime_type", ""),
                original_filename=item.get("original_filename", ""),
                source_local_path=item.get("source_local_path", ""),
                exported_path=item.get("exported_path", ""),
                file_size=item.get("file_size"),
                sha256=item.get("sha256", ""),
                status=item.get("status", ""),
                notes=item.get("notes", ""),
                record_key=item.get(
                    "record_key",
                    f"{item.get('chat_id', '')}|{item.get('message_id', '')}|{item.get('source_local_path', '')}",
                ),
            )
            loaded[record.record_key] = record
        return loaded

    def _write_outputs(
        self,
        summary: ExportSummary,
        records: list[ManifestRecord],
        unresolved: list[dict[str, Any]],
    ) -> None:
        manifest_payload = {
            "generated_at": summary.generated_at,
            "scan": self.scan.to_dict(),
            "summary": summary.to_dict(),
            "records": [record.to_public_dict() | {"record_key": record.record_key} for record in records],
        }
        with self.manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest_payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

        with self.unresolved_path.open("w", encoding="utf-8") as handle:
            json.dump(unresolved, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

        summary_lines = [
            f"Generated at: {summary.generated_at}",
            f"Run mode: {summary.run_mode}",
            f"Output directory: {summary.output_dir}",
            f"Primary database: {summary.primary_database or 'not found'}",
            f"Selected types: {', '.join(summary.selected_types)}",
            f"Total chats in DB: {summary.total_chats}",
            f"Total messages in DB: {summary.total_messages}",
            f"Total media rows in DB: {summary.total_media_rows}",
            f"Target records: {summary.target_records}",
            f"Exported records: {summary.exported_records}",
            f"Duplicate records: {summary.duplicate_records}",
            f"Dry-run records: {summary.dry_run_records}",
            f"Unresolved records: {summary.unresolved_records}",
            f"Unique exported files: {summary.unique_exported_files}",
            f"Total exported bytes this run: {summary.total_exported_bytes}",
            "",
            "Notes:",
        ]
        summary_lines.extend(f"- {note}" for note in summary.notes)
        with self.summary_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(summary_lines).rstrip() + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _classify_attachment(media_local_path: str | None, media_url: str | None) -> str | None:
    suffix = Path(media_local_path or "").suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in PDF_SUFFIXES:
        return "pdf"

    url_path = urlparse(media_url or "").path.lower()
    if any(url_path.endswith(ext) for ext in VIDEO_SUFFIXES):
        return "video"
    if any(url_path.endswith(ext) for ext in PDF_SUFFIXES):
        return "pdf"
    return None


def _infer_mime_type(path_string: str) -> str:
    suffix = Path(path_string).suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix == ".m4v":
        return "video/x-m4v"

    mime_type, _ = mimetypes.guess_type(path_string)
    return mime_type or "application/octet-stream"


def _infer_original_filename(media_title: str | None, media_local_path: str | None) -> tuple[str, str]:
    path_name = Path(media_local_path or "").name
    if media_title:
        cleaned = media_title.strip()
        if cleaned:
            parsed = urlparse(cleaned)
            if parsed.scheme and Path(parsed.path).suffix.lower() in VIDEO_SUFFIXES | PDF_SUFFIXES:
                return Path(parsed.path).name, "original filename inferred from media title URL"
            if Path(cleaned).suffix.lower() in VIDEO_SUFFIXES | PDF_SUFFIXES:
                return Path(cleaned).name, "original filename inferred from media title"
            return path_name, "media title did not look like a stable filename; using local basename"
    return path_name, "original filename unavailable in observed schema; using local basename"


def _derive_sender(
    is_from_me: bool,
    from_jid: str | None,
    to_jid: str | None,
    group_member_jid: str | None,
    group_contact_name: str | None,
    group_first_name: str | None,
    chat_id: str | None,
) -> str:
    if is_from_me:
        return "me"
    if group_contact_name:
        return group_contact_name
    if group_first_name:
        return group_first_name
    if group_member_jid:
        return group_member_jid
    return from_jid or to_jid or chat_id or "unknown"


def _apple_timestamp_to_iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    timestamp = APPLE_EPOCH + timedelta(seconds=seconds)
    return timestamp.astimezone().isoformat(timespec="seconds")


def _chat_id_from_relative_path(path_string: str | None) -> str:
    parts = Path(path_string or "").parts
    if len(parts) >= 2 and parts[0] in {"Media", "Message"}:
        return parts[1]
    return ""


def _append_note(existing: str, new_note: str) -> str:
    if not new_note:
        return existing
    if not existing:
        return new_note
    if new_note in existing:
        return existing
    return f"{existing}; {new_note}"


def _merge_notes(*notes: str) -> str:
    merged = ""
    for note in notes:
        merged = _append_note(merged, note)
    return merged


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
