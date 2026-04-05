from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fallback-ui",
        description=(
            "Brittle fallback mode for cases where direct local storage parsing stops working. "
            "This mode is intentionally secondary and requires WhatsApp plus macOS Accessibility access."
        ),
    )
    parser.add_argument(
        "--chat-name",
        action="append",
        default=[],
        help="Chat names to search for in WhatsApp. Repeat for multiple chats.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the AppleScript that would be executed without sending it to osascript.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.chat_name:
        parser.error("At least one --chat-name is required for fallback mode.")

    script = _build_applescript(args.chat_name)
    if args.dry_run:
        print(script)
        return 0

    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.strip())
        return completed.returncode
    return 0


def _build_applescript(chat_names: list[str]) -> str:
    quoted = ", ".join(f"\"{name}\"" for name in chat_names)
    return f"""
set targetChats to {{{quoted}}}
tell application "WhatsApp" to activate
delay 1
tell application "System Events"
    tell process "WhatsApp"
        repeat with targetChat in targetChats
            keystroke "f" using {{command down}}
            delay 0.4
            keystroke targetChat
            delay 0.8
            key code 36
            delay 1
        end repeat
    end tell
end tell
return "Fallback UI search sequence completed."
""".strip()
