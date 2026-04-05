from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import hexdigits
from typing import Any


@dataclass
class ManifestTableEvidence:
    table_name: str
    columns: list[str]
    row_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackupStructureEvidence:
    backup_path: str
    top_level_entries: list[str]
    shard_directories: list[str]
    shard_directory_count: int
    sample_shard_files: list[str]
    manifest_db_path: str | None
    manifest_db_opened_path: str | None
    manifest_db_accessible: bool
    manifest_db_error: str
    manifest_db_sqlite_header: bool | None = None
    manifest_db_header_hex: str = ""
    manifest_tables: list[ManifestTableEvidence] = field(default_factory=list)
    probable_mapping_table: str | None = None
    probable_mapping_columns: list[str] = field(default_factory=list)
    sample_mapping_rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_path": self.backup_path,
            "top_level_entries": self.top_level_entries,
            "shard_directories": self.shard_directories,
            "shard_directory_count": self.shard_directory_count,
            "sample_shard_files": self.sample_shard_files,
            "manifest_db_path": self.manifest_db_path,
            "manifest_db_opened_path": self.manifest_db_opened_path,
            "manifest_db_accessible": self.manifest_db_accessible,
            "manifest_db_error": self.manifest_db_error,
            "manifest_db_sqlite_header": self.manifest_db_sqlite_header,
            "manifest_db_header_hex": self.manifest_db_header_hex,
            "manifest_tables": [table.to_dict() for table in self.manifest_tables],
            "probable_mapping_table": self.probable_mapping_table,
            "probable_mapping_columns": self.probable_mapping_columns,
            "sample_mapping_rows": self.sample_mapping_rows,
            "notes": self.notes,
        }


def inspect_backup_structure(
    backup_path: str,
    manifest_db_override: str | None = None,
) -> BackupStructureEvidence:
    if not backup_path:
        return BackupStructureEvidence(
            backup_path="",
            top_level_entries=[],
            shard_directories=[],
            shard_directory_count=0,
            sample_shard_files=[],
            manifest_db_path=None,
            manifest_db_opened_path=None,
            manifest_db_accessible=False,
            manifest_db_error="No backup path was available for inspection.",
            notes=["No backup was selected, so structure inspection could not proceed."],
        )
    root = Path(backup_path)
    try:
        top_level_items = sorted(item.name for item in root.iterdir())
    except PermissionError as exc:
        return BackupStructureEvidence(
            backup_path=str(root),
            top_level_entries=[],
            shard_directories=[],
            shard_directory_count=0,
            sample_shard_files=[],
            manifest_db_path=None,
            manifest_db_opened_path=None,
            manifest_db_accessible=False,
            manifest_db_error=str(exc),
            notes=["Backup root could not be read due to macOS permissions."],
        )

    shard_directories = [
        name
        for name in top_level_items
        if len(name) == 2 and all(char in hexdigits for char in name)
    ]
    sample_files = _sample_shard_files(root, shard_directories)
    manifest_db = root / "Manifest.db"
    if not manifest_db.exists():
        return BackupStructureEvidence(
            backup_path=str(root),
            top_level_entries=top_level_items,
            shard_directories=shard_directories[:20],
            shard_directory_count=len(shard_directories),
            sample_shard_files=sample_files,
            manifest_db_path=None,
            manifest_db_opened_path=None,
            manifest_db_accessible=False,
            manifest_db_error="Manifest.db not present.",
            notes=["No Manifest.db file was found in the selected backup."],
        )

    header = _read_manifest_header(manifest_db)
    manifest_db_sqlite_header = header.startswith(b"SQLite format 3\x00") if header else None
    header_hex = header.hex() if header else ""
    opened_manifest_db = Path(manifest_db_override) if manifest_db_override else manifest_db
    if not manifest_db_override and manifest_db_sqlite_header is False:
        return BackupStructureEvidence(
            backup_path=str(root),
            top_level_entries=top_level_items,
            shard_directories=shard_directories[:20],
            shard_directory_count=len(shard_directories),
            sample_shard_files=sample_files,
            manifest_db_path=str(manifest_db),
            manifest_db_opened_path=None,
            manifest_db_accessible=False,
            manifest_db_error=(
                "Manifest.db does not start with a SQLite header and appears encrypted or otherwise opaque on disk."
            ),
            manifest_db_sqlite_header=False,
            manifest_db_header_hex=header_hex,
            notes=[
                "Manifest.db exists, but its on-disk header does not match 'SQLite format 3'.",
                "A decrypted working copy of Manifest.db is required before schema inspection can continue.",
            ],
        )

    return _inspect_manifest_db(
        backup_path=root,
        manifest_db=opened_manifest_db,
        manifest_db_source_path=manifest_db,
        top_level_entries=top_level_items,
        shard_directories=shard_directories,
        sample_files=sample_files,
        manifest_db_sqlite_header=manifest_db_sqlite_header,
        manifest_db_header_hex=header_hex,
    )


def _sample_shard_files(root: Path, shard_directories: list[str], limit: int = 10) -> list[str]:
    samples: list[str] = []
    for shard_name in shard_directories[:5]:
        shard_path = root / shard_name
        try:
            for child in sorted(shard_path.iterdir()):
                if child.is_file():
                    samples.append(str(child.relative_to(root)))
                if len(samples) >= limit:
                    return samples
        except PermissionError:
            continue
    return samples


def _inspect_manifest_db(
    backup_path: Path,
    manifest_db: Path,
    manifest_db_source_path: Path,
    top_level_entries: list[str],
    shard_directories: list[str],
    sample_files: list[str],
    manifest_db_sqlite_header: bool | None,
    manifest_db_header_hex: str,
) -> BackupStructureEvidence:
    try:
        connection = sqlite3.connect(f"file:{manifest_db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return BackupStructureEvidence(
            backup_path=str(backup_path),
            top_level_entries=top_level_entries,
            shard_directories=shard_directories[:20],
            shard_directory_count=len(shard_directories),
            sample_shard_files=sample_files,
            manifest_db_path=str(manifest_db_source_path),
            manifest_db_opened_path=str(manifest_db),
            manifest_db_accessible=False,
            manifest_db_error=str(exc),
            manifest_db_sqlite_header=manifest_db_sqlite_header,
            manifest_db_header_hex=manifest_db_header_hex,
            notes=["Manifest.db exists but could not be opened read-only."],
        )

    tables: list[ManifestTableEvidence] = []
    probable_mapping_table = None
    probable_mapping_columns: list[str] = []
    sample_mapping_rows: list[dict[str, Any]] = []
    notes: list[str] = []

    try:
        with connection:
            table_names = [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            for table_name in table_names:
                columns = _table_columns(connection, table_name)
                row_count = _table_count(connection, table_name)
                tables.append(
                    ManifestTableEvidence(
                        table_name=table_name,
                        columns=columns,
                        row_count=row_count,
                    )
                )
                lower_columns = {column.lower() for column in columns}
                if probable_mapping_table is None and {
                    "fileid",
                    "domain",
                    "relativepath",
                }.issubset(lower_columns):
                    probable_mapping_table = table_name
                    probable_mapping_columns = columns

            if probable_mapping_table:
                sample_mapping_rows = _sample_mapping_rows(
                    connection,
                    probable_mapping_table,
                    probable_mapping_columns,
                )
                notes.append(
                    "A probable file-mapping table was found because it exposes fileID, domain, and relativePath columns."
                )
            else:
                notes.append(
                    "No obvious file-mapping table was detected from table/column names alone."
                )
            if manifest_db != manifest_db_source_path:
                notes.append(
                    "Schema inspection used a decrypted working copy of Manifest.db generated outside the source backup."
                )
    except sqlite3.Error as exc:
        return BackupStructureEvidence(
            backup_path=str(backup_path),
            top_level_entries=top_level_entries,
            shard_directories=shard_directories[:20],
            shard_directory_count=len(shard_directories),
            sample_shard_files=sample_files,
            manifest_db_path=str(manifest_db_source_path),
            manifest_db_opened_path=str(manifest_db),
            manifest_db_accessible=False,
            manifest_db_error=str(exc),
            manifest_db_sqlite_header=manifest_db_sqlite_header,
            manifest_db_header_hex=manifest_db_header_hex,
            notes=["Manifest.db opened, but schema inspection failed before results could be collected."],
        )
    finally:
        connection.close()

    return BackupStructureEvidence(
        backup_path=str(backup_path),
        top_level_entries=top_level_entries,
        shard_directories=shard_directories[:20],
        shard_directory_count=len(shard_directories),
        sample_shard_files=sample_files,
        manifest_db_path=str(manifest_db_source_path),
        manifest_db_opened_path=str(manifest_db),
        manifest_db_accessible=True,
        manifest_db_error="",
        manifest_db_sqlite_header=manifest_db_sqlite_header,
        manifest_db_header_hex=manifest_db_header_hex,
        manifest_tables=tables,
        probable_mapping_table=probable_mapping_table,
        probable_mapping_columns=probable_mapping_columns,
        sample_mapping_rows=sample_mapping_rows,
        notes=notes,
    )


def _table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    query = f'PRAGMA table_info("{table_name}")'
    return [row[1] for row in connection.execute(query)]


def _table_count(connection: sqlite3.Connection, table_name: str) -> int | None:
    try:
        row = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row is not None else None


def _sample_mapping_rows(
    connection: sqlite3.Connection,
    table_name: str,
    columns: list[str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    wanted = []
    lower_to_original = {column.lower(): column for column in columns}
    for name in ("fileid", "domain", "relativepath", "flags"):
        if name in lower_to_original:
            wanted.append(lower_to_original[name])
    column_sql = ", ".join(f'"{column}"' for column in wanted)
    query = f'SELECT {column_sql} FROM "{table_name}" LIMIT {limit}'
    rows: list[dict[str, Any]] = []
    for row in connection.execute(query):
        row_dict = {}
        for key in row.keys():
            value = row[key]
            row_dict[key] = value if isinstance(value, (str, int, float, type(None))) else str(value)
        rows.append(row_dict)
    return rows


def _read_manifest_header(path: Path, size: int = 32) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return b""
