from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backup_locator import BackupCandidate
from backup_manifest_parser import BackupStructureEvidence


@dataclass
class WhatsAppLocationEvidence:
    located: bool | None
    search_performed: bool
    candidate_rows: list[dict[str, Any]] = field(default_factory=list)
    candidate_row_count: int | None = None
    probable_domains: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def locate_whatsapp_records(
    candidate: BackupCandidate | None,
    structure: BackupStructureEvidence,
) -> WhatsAppLocationEvidence:
    manifest_plist_ids = candidate.whatsapp_application_ids if candidate else []
    if not structure.manifest_db_path or not structure.manifest_db_accessible:
        return WhatsAppLocationEvidence(
            located=bool(manifest_plist_ids) or None,
            search_performed=False,
            probable_domains=manifest_plist_ids,
            notes=(
                [
                    "WhatsApp-related application/app-group identifiers were found in Manifest.plist, but file-level backup records are not searchable until Manifest.db is decrypted."
                ]
                if manifest_plist_ids
                else ["Manifest.db could not be searched for WhatsApp records."]
            ),
        )

    if structure.probable_mapping_table is None:
        return WhatsAppLocationEvidence(
            located=bool(manifest_plist_ids) or None,
            search_performed=False,
            probable_domains=manifest_plist_ids,
            notes=[
                "No mapping table with fileID/domain/relativePath columns was identified, so WhatsApp search could not proceed."
            ],
        )

    manifest_db_path = structure.manifest_db_opened_path or structure.manifest_db_path
    if not manifest_db_path:
        return WhatsAppLocationEvidence(
            located=bool(manifest_plist_ids) or None,
            search_performed=False,
            probable_domains=manifest_plist_ids,
            notes=["No readable Manifest.db path was available for WhatsApp record search."],
        )
    manifest_db = Path(manifest_db_path)
    lower_to_original = {
        column.lower(): column for column in structure.probable_mapping_columns
    }
    file_id_column = lower_to_original.get("fileid", "fileID")
    domain_column = lower_to_original.get("domain", "domain")
    relative_path_column = lower_to_original.get("relativepath", "relativePath")
    rows: list[dict[str, Any]] = []
    domains: set[str] = set()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{manifest_db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        with connection:
            query = (
                f'SELECT "{file_id_column}", "{domain_column}", "{relative_path_column}" '
                f'FROM "{structure.probable_mapping_table}" '
                f'WHERE lower(COALESCE("{domain_column}", \'\')) LIKE \'%whatsapp%\' '
                f'   OR lower(COALESCE("{relative_path_column}", \'\')) LIKE \'%whatsapp%\' '
                "LIMIT 100"
            )
            for row in connection.execute(query):
                row_dict = {key: row[key] for key in row.keys()}
                rows.append(row_dict)
                domain = row_dict.get(domain_column)
                if isinstance(domain, str) and domain:
                    domains.add(domain)

            count_row = connection.execute(
                f'SELECT COUNT(*) AS count FROM "{structure.probable_mapping_table}" '
                f'WHERE lower(COALESCE("{domain_column}", \'\')) LIKE \'%whatsapp%\' '
                f'   OR lower(COALESCE("{relative_path_column}", \'\')) LIKE \'%whatsapp%\''
            ).fetchone()
    except sqlite3.Error as exc:
        return WhatsAppLocationEvidence(
            located=bool(manifest_plist_ids) or None,
            search_performed=False,
            probable_domains=manifest_plist_ids,
            notes=[
                f"Manifest.db search failed after decryption with SQLite error: {exc}",
                *(
                    [
                        "Manifest.plist still confirms WhatsApp application/app-group presence in this backup."
                    ]
                    if manifest_plist_ids
                    else []
                ),
            ],
        )
    finally:
        try:
            connection.close()
        except Exception:
            pass

    return WhatsAppLocationEvidence(
        located=bool(rows) or bool(manifest_plist_ids),
        search_performed=True,
        candidate_rows=rows,
        candidate_row_count=int(count_row["count"]) if count_row is not None else None,
        probable_domains=sorted(domains | set(manifest_plist_ids)),
        notes=(
            ["Manifest.db was searched for rows whose domain or relative path contains 'whatsapp'."]
            if rows
            else [
                "No rows containing 'whatsapp' were found in the accessible mapping table.",
                *(
                    [
                        "Manifest.plist still confirms WhatsApp application/app-group presence in this backup."
                    ]
                    if manifest_plist_ids
                    else []
                ),
            ]
        ),
    )
