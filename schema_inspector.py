from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SqliteSchemaEvidence:
    path: str
    accessible: bool
    error: str
    tables: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_sqlite_file(path: str) -> SqliteSchemaEvidence:
    database_path = Path(path)
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return SqliteSchemaEvidence(
            path=str(database_path),
            accessible=False,
            error=str(exc),
        )

    tables: list[dict[str, Any]] = []
    with connection:
        table_names = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        for table_name in table_names:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')]
            try:
                count = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            except sqlite3.Error:
                count = None
            tables.append(
                {
                    "table_name": table_name,
                    "columns": columns,
                    "row_count": count,
                }
            )

    return SqliteSchemaEvidence(
        path=str(database_path),
        accessible=True,
        error="",
        tables=tables,
    )
