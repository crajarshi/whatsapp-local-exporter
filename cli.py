from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from attachment_enumerator import enumerate_attachments
from backup_decryptor import assess_decryption
from backup_locator import BackupCandidate, BackupDiscoveryResult, discover_backups
from backup_manifest_parser import inspect_backup_structure
from exporter import export_records
from manifest import DryRunSummary, write_artifacts
from utils import (
    BACKUP_PASSWORD_ENV_VAR,
    DEFAULT_OUTPUT_DIR,
    bool_to_text,
    ensure_output_dir,
    format_bytes,
    now_iso,
    prompt_password,
)
from whatsapp_locator import locate_whatsapp_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finder-backup-whatsapp-investigator",
        description=(
            "Investigation-first CLI for encrypted Finder iPhone backups that tries to locate "
            "and extract WhatsApp backup contents such as videos, images, audio, documents, and raw chat databases."
        ),
    )
    parser.add_argument("--list-backups", action="store_true", help="List candidate Finder backups.")
    parser.add_argument("--backup-path", help="Use a specific Finder backup directory.")
    parser.add_argument("--scan", action="store_true", help="Inspect the selected backup structure.")
    parser.add_argument("--dry-run", action="store_true", help="Perform a non-exporting investigation pass.")
    parser.add_argument("--export", action="store_true", help="Attempt export after investigation proves possible.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for manifest.json, summary.txt, unresolved.json, and exported files.",
    )
    parser.add_argument(
        "--types",
        default="video,pdf",
        help="Comma-separated categories to target: video,image,audio,document,chat,database,other,all.",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse already exported files and prior manifest state.")
    parser.add_argument("--verbose", action="store_true", help="Print verbose JSON output.")
    parser.add_argument(
        "--password-prompt",
        action="store_true",
        help="Securely prompt for the encrypted backup password using hidden terminal input.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not any((args.list_backups, args.scan, args.dry_run, args.export)):
        parser.error("At least one of --list-backups, --scan, --dry-run, or --export is required.")

    selected_types = _parse_types(args.types, parser)
    discovery = discover_backups(args.backup_path)
    output_dir = ensure_output_dir(args.output)

    if args.list_backups:
        print(json.dumps(discovery.to_dict(), indent=2, ensure_ascii=False))
        if not any((args.scan, args.dry_run, args.export)):
            return 0 if discovery.accessible else 1

    candidate = _select_candidate(discovery)
    try:
        password = prompt_password(args.password_prompt)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    decryption = assess_decryption(candidate, password, output_dir=output_dir)
    structure = (
        inspect_backup_structure(
            candidate.backup_path,
            manifest_db_override=decryption.decrypted_manifest_db_path,
        )
        if candidate
        else inspect_backup_structure("")
    )
    whatsapp = locate_whatsapp_records(candidate, structure)
    enumeration = enumerate_attachments(
        candidate=candidate,
        structure=structure,
        whatsapp=whatsapp,
        types=selected_types,
        output_dir=output_dir,
    )
    if not discovery.accessible and discovery.error:
        enumeration.unresolved.insert(
            0,
            {
                "status": "unresolved",
                "notes": (
                    "Finder backup discovery is currently blocked by macOS permissions: "
                    f"{discovery.error}"
                ),
            },
        )
        enumeration.total_unresolved_records = len(enumeration.unresolved)
    if candidate and not structure.manifest_db_accessible and structure.manifest_db_error:
        enumeration.unresolved.insert(
            0,
            {
                "status": "unresolved",
                "notes": structure.manifest_db_error,
            },
        )
        enumeration.total_unresolved_records = len(enumeration.unresolved)

    summary = DryRunSummary(
        generated_at=now_iso(),
        selected_backup_path=candidate.backup_path if candidate else (args.backup_path or discovery.requested_root),
        backup_id=candidate.backup_id if candidate else "",
        backup_encrypted=decryption.backup_encrypted,
        decryption_succeeded=decryption.decryption_succeeded,
        whatsapp_data_located=whatsapp.located,
        total_whatsapp_records=enumeration.total_whatsapp_records,
        total_whatsapp_file_records=enumeration.total_whatsapp_file_records,
        total_whatsapp_file_bytes=enumeration.total_whatsapp_file_bytes,
        total_media_file_records=enumeration.total_media_file_records,
        total_media_file_bytes=enumeration.total_media_file_bytes,
        total_chats_discovered=enumeration.total_chats_discovered,
        total_messages_discovered=enumeration.total_messages_discovered,
        total_attachment_records_discovered=enumeration.total_attachment_records_discovered,
        total_export_candidate_bytes=enumeration.total_export_candidate_bytes,
        total_video_records=enumeration.total_video_records,
        total_video_bytes=enumeration.total_video_bytes,
        total_pdf_document_records=enumeration.total_pdf_document_records,
        total_pdf_document_bytes=enumeration.total_pdf_document_bytes,
        export_category_counts=enumeration.export_category_counts,
        export_category_bytes=enumeration.export_category_bytes,
        total_records_with_resolvable_local_content=enumeration.total_records_with_resolvable_local_content,
        total_metadata_only_records=enumeration.total_metadata_only_records,
        total_unresolved_records=enumeration.total_unresolved_records,
        notes=_summary_notes(discovery, candidate, decryption, structure, whatsapp, enumeration),
    )

    investigation_payload = {
        "discovery": discovery.to_dict(),
        "selected_backup": candidate.to_dict() if candidate else None,
        "decryption": decryption.to_dict(),
        "structure": structure.to_dict(),
        "whatsapp": whatsapp.to_dict(),
        "enumeration": enumeration.to_dict(),
        "selected_types": selected_types,
    }
    write_artifacts(
        output_dir=output_dir,
        summary=summary,
        records=enumeration.records,
        unresolved=enumeration.unresolved,
        investigation_payload=investigation_payload,
    )

    if args.verbose:
        print(json.dumps(investigation_payload, indent=2, ensure_ascii=False))
        print()
    if args.scan or args.dry_run or args.verbose:
        print(_render_console_summary(summary))

    if args.export:
        print(_render_pre_export_report(summary))
        result = export_records(
            candidate=candidate,
            records=enumeration.records,
            output_dir=output_dir,
            types=selected_types,
            resume=args.resume,
            password=password,
        )
        summary.notes.extend(result.notes)
        write_artifacts(
            output_dir=output_dir,
            summary=summary,
            records=enumeration.records,
            unresolved=enumeration.unresolved,
            investigation_payload={
                **investigation_payload,
                "export": result.to_dict(),
            },
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        password = None
        return 0 if result.attempted else 1

    password = None

    return 0 if candidate is not None and discovery.accessible else 1


def _parse_types(raw_types: str, parser: argparse.ArgumentParser) -> list[str]:
    aliases = {"pdf": "document", "docs": "document", "images": "image", "videos": "video", "audios": "audio"}
    selected = [aliases.get(item.strip().lower(), item.strip().lower()) for item in raw_types.split(",") if item.strip()]
    if "all" in selected:
        return ["all"]
    invalid = [item for item in selected if item not in {"video", "image", "audio", "document", "chat", "database", "other"}]
    if invalid:
        parser.error(f"Unsupported --types values: {', '.join(invalid)}")
    return selected


def _select_candidate(discovery: BackupDiscoveryResult) -> BackupCandidate | None:
    if not discovery.candidates:
        return None
    if len(discovery.candidates) == 1:
        return discovery.candidates[0]
    for candidate in discovery.candidates:
        if candidate.manifest_db_path:
            return candidate
    return discovery.candidates[0]


def _summary_notes(*parts: object) -> list[str]:
    notes: list[str] = []
    for part in parts:
        if part is None:
            continue
        part_error = getattr(part, "error", None)
        if isinstance(part_error, str) and part_error:
            notes.append(part_error)
        manifest_db_error = getattr(part, "manifest_db_error", None)
        if isinstance(manifest_db_error, str) and manifest_db_error:
            notes.append(manifest_db_error)
        part_notes = getattr(part, "notes", None)
        if isinstance(part_notes, list):
            notes.extend(str(item) for item in part_notes)
    if any("environment variable" in note.lower() for note in notes):
        notes.append(
            f"A password can also be supplied through the {BACKUP_PASSWORD_ENV_VAR} environment variable, but hidden terminal prompting remains the preferred mode."
        )
    return notes


def _render_console_summary(summary: DryRunSummary) -> str:
    lines = [
        "Dry-run summary",
        f"selected backup path: {summary.selected_backup_path or 'unknown'}",
        f"backup encrypted: {bool_to_text(summary.backup_encrypted)}",
        f"decryption succeeded: {bool_to_text(summary.decryption_succeeded)}",
        f"whatsapp data located: {bool_to_text(summary.whatsapp_data_located)}",
        f"total WhatsApp rows: {_fmt(summary.total_whatsapp_records)}",
        f"total WhatsApp file records: {_fmt(summary.total_whatsapp_file_records)}",
        f"total WhatsApp file bytes: {_fmt_bytes(summary.total_whatsapp_file_bytes)}",
        f"total media file records: {_fmt(summary.total_media_file_records)}",
        f"total media file bytes: {_fmt_bytes(summary.total_media_file_bytes)}",
        f"total chats discovered: {_fmt(summary.total_chats_discovered)}",
        f"total messages discovered: {_fmt(summary.total_messages_discovered)}",
        f"total attachment records discovered: {_fmt(summary.total_attachment_records_discovered)}",
        f"total export candidate bytes: {_fmt_bytes(summary.total_export_candidate_bytes)}",
        f"total video records: {_fmt(summary.total_video_records)}",
        f"total video bytes: {_fmt_bytes(summary.total_video_bytes)}",
        f"total pdf/document records: {_fmt(summary.total_pdf_document_records)}",
        f"total pdf/document bytes: {_fmt_bytes(summary.total_pdf_document_bytes)}",
        f"total records with resolvable local content: {_fmt(summary.total_records_with_resolvable_local_content)}",
        f"total metadata-only records: {_fmt(summary.total_metadata_only_records)}",
        f"total unresolved records: {_fmt(summary.total_unresolved_records)}",
    ]
    if summary.export_category_counts:
        lines.append("export category breakdown:")
        for category in sorted(summary.export_category_counts):
            lines.append(
                f"- {category}: {_fmt(summary.export_category_counts.get(category))} / "
                f"{_fmt_bytes(summary.export_category_bytes.get(category))}"
            )
    return "\n".join(lines)


def _render_pre_export_report(summary: DryRunSummary) -> str:
    lines = [
        "Pre-export report",
        f"WhatsApp total file bytes: {_fmt_bytes(summary.total_whatsapp_file_bytes)}",
        f"WhatsApp media file bytes: {_fmt_bytes(summary.total_media_file_bytes)}",
        f"Target export bytes: {_fmt_bytes(summary.total_export_candidate_bytes)}",
        f"Target chats: {_fmt(summary.total_chats_discovered)}",
    ]
    if summary.export_category_counts:
        lines.append("Target categories:")
        for category in sorted(summary.export_category_counts):
            lines.append(
                f"- {category}: {_fmt(summary.export_category_counts.get(category))} files / "
                f"{_fmt_bytes(summary.export_category_bytes.get(category))}"
            )
    else:
        lines.extend(
            [
                f"Target video files: {_fmt(summary.total_video_records)}",
                f"Target video bytes: {_fmt_bytes(summary.total_video_bytes)}",
                f"Target pdf/document files: {_fmt(summary.total_pdf_document_records)}",
                f"Target pdf/document bytes: {_fmt_bytes(summary.total_pdf_document_bytes)}",
            ]
        )
    return "\n".join(lines)


def _fmt(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _fmt_bytes(value: int | None) -> str:
    return "unknown" if value is None else f"{value} ({format_bytes(value)})"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
