from __future__ import annotations

import argparse
import json
from pathlib import Path

from whatsapp_local_exporter.discovery import discover_storage
from whatsapp_local_exporter.exporter import ExporterError, WhatsAppLocalExporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsapp-local-exporter",
        description=(
            "Export locally available WhatsApp macOS videos and PDFs by reading local storage directly."
        ),
    )
    parser.add_argument("--scan", action="store_true", help="Discover WhatsApp storage paths and databases.")
    parser.add_argument("--dry-run", action="store_true", help="Enumerate records without copying files.")
    parser.add_argument("--export", action="store_true", help="Copy matching files into the output directory.")
    parser.add_argument(
        "--output",
        default="output",
        help="Directory for manifest.json, summary.txt, unresolved.json, and exported files.",
    )
    parser.add_argument(
        "--types",
        default="video,pdf",
        help="Comma-separated attachment types to target. Supported: video,pdf",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse an existing manifest.json in the output directory.")
    parser.add_argument("--verbose", action="store_true", help="Print extra scan and run details.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not any((args.scan, args.dry_run, args.export)):
        parser.error("At least one of --scan, --dry-run, or --export is required.")

    selected_types = _parse_types(args.types, parser=parser)
    scan = discover_storage()

    if args.verbose or args.scan:
        print(json.dumps(scan.to_dict(), indent=2, ensure_ascii=False))

    if args.dry_run or args.export:
        exporter = WhatsAppLocalExporter(
            scan=scan,
            output_dir=Path(args.output).expanduser().resolve(),
            verbose=args.verbose,
        )
        try:
            summary, _ = exporter.run(
                selected_types=selected_types,
                do_export=args.export,
                dry_run=args.dry_run,
                resume=args.resume,
            )
        except ExporterError as exc:
            print(f"error: {exc}")
            return 1

        if args.verbose:
            print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))

    return 0


def _parse_types(raw_types: str, parser: argparse.ArgumentParser) -> list[str]:
    selected = [item.strip().lower() for item in raw_types.split(",") if item.strip()]
    invalid = [item for item in selected if item not in {"video", "pdf"}]
    if invalid:
        parser.error(f"Unsupported --types entries: {', '.join(invalid)}")
    return selected
