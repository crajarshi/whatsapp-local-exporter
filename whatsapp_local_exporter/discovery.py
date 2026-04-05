from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


KNOWN_ROOTS = {
    "modern_container": Path("~/Library/Containers/net.whatsapp.WhatsApp").expanduser(),
    "legacy_container": Path("~/Library/Containers/desktop.WhatsApp").expanduser(),
    "modern_group_private": Path(
        "~/Library/Group Containers/group.net.whatsapp.WhatsApp.private"
    ).expanduser(),
    "modern_group_shared": Path(
        "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
    ).expanduser(),
    "legacy_group_shared": Path(
        "~/Library/Group Containers/57T9237FN3.desktop.WhatsApp"
    ).expanduser(),
    "modern_group_smb": Path(
        "~/Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared"
    ).expanduser(),
    "family_group": Path("~/Library/Group Containers/group.net.whatsapp.family").expanduser(),
}

INTERESTING_SUFFIXES = {
    ".sqlite",
    ".db",
    ".sqlite-shm",
    ".sqlite-wal",
    ".json",
    ".plist",
    ".ldb",
}

INTERESTING_DIR_NAMES = {
    "Media",
    "Message",
    "Caches",
    "CoreData",
    "IndexedDB",
    "Local Storage",
    "blob_storage",
    "MediaDownload",
    "MediaStreaming",
    "LevelDB",
}


@dataclass
class ScanHit:
    category: str
    path: str


@dataclass
class StorageScan:
    existing_roots: dict[str, str]
    database_candidates: list[str]
    media_base_dirs: list[str]
    hits: list[ScanHit] = field(default_factory=list)
    primary_database: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "existing_roots": self.existing_roots,
            "database_candidates": self.database_candidates,
            "media_base_dirs": self.media_base_dirs,
            "hits": [asdict(hit) for hit in self.hits],
            "primary_database": self.primary_database,
        }


def _walk_limited(root: Path, max_depth: int) -> Iterable[Path]:
    if not root.exists():
        return []

    results: list[Path] = []
    root_parts = len(root.parts)
    stack = [root]
    while stack:
        current = stack.pop()
        results.append(current)
        if len(current.parts) - root_parts >= max_depth:
            continue
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in reversed(children):
            if child.is_dir():
                stack.append(child)
            else:
                results.append(child)
    return results


def discover_storage(max_depth: int = 5) -> StorageScan:
    existing_roots = {
        label: str(path)
        for label, path in KNOWN_ROOTS.items()
        if path.exists()
    }

    hits: list[ScanHit] = []
    database_candidates: list[str] = []
    media_base_dirs: list[str] = []

    for label, root_string in existing_roots.items():
        root = Path(root_string)
        for item in _walk_limited(root, max_depth=max_depth):
            if item.is_file() and item.suffix.lower() in INTERESTING_SUFFIXES:
                category = item.suffix.lower().lstrip(".")
                hits.append(ScanHit(category=category, path=str(item)))
                if item.name in {"ChatStorage.sqlite", "ExtChatDatabase.sqlite"} or item.suffix.lower() in {
                    ".sqlite",
                    ".db",
                }:
                    database_candidates.append(str(item))
            elif item.is_dir() and item.name in INTERESTING_DIR_NAMES:
                hits.append(ScanHit(category="dir", path=str(item)))
                media_base_dirs.append(str(item))

        if label == "modern_group_shared":
            preferred_media = [
                root / "Message",
                root / "Message" / "Media",
                root / "Media",
                root / "Library" / "Caches" / "MediaDownload",
                root / "Library" / "Caches" / "MediaStreaming",
            ]
            for candidate in preferred_media:
                if candidate.exists():
                    media_base_dirs.append(str(candidate))

    database_candidates = _unique(database_candidates)
    media_base_dirs = _unique(media_base_dirs)

    primary_database = None
    for preferred in (
        KNOWN_ROOTS["modern_group_shared"] / "ChatStorage.sqlite",
        KNOWN_ROOTS["legacy_group_shared"] / "ChatStorage.sqlite",
    ):
        if preferred.exists():
            primary_database = str(preferred)
            break
    if primary_database is None and database_candidates:
        primary_database = database_candidates[0]

    return StorageScan(
        existing_roots=existing_roots,
        database_candidates=database_candidates,
        media_base_dirs=media_base_dirs,
        hits=hits,
        primary_database=primary_database,
    )


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered
