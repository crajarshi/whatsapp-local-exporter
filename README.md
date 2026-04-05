# WhatsApp Export From Encrypted iPhone Backup

This is a macOS-first Python CLI for exporting WhatsApp data from an encrypted iPhone Finder backup on disk.

## Requirements

- macOS
- Python 3.9+
- an iPhone
- a USB cable
- an encrypted Finder backup created on this Mac
- the encrypted backup password
- Full Disk Access for Codex/Terminal if needed

Default Finder backup root:

- `~/Library/Application Support/MobileSync/Backup/`

Default export output directory:

- `~/Downloads/whatsapp-export`

## Quick Start

### Step 1. Create the encrypted Finder backup

Follow Apple’s instructions for making a local Mac backup and for turning on encrypted local backups:

- [How to back up your iPhone, iPad, and iPod touch with your Mac](https://support.apple.com/en-us/108796)
- [About encrypted backups on your iPhone, iPad, or iPod touch](https://support.apple.com/en-us/108353)

In practice, the Finder flow is:

1. Connect the iPhone to your Mac with a USB cable.
2. Open Finder and select the iPhone in the sidebar.
3. Open the `General` tab.
4. Choose `Back up all of the data on your iPhone to this Mac`.
5. Turn on `Encrypt local backup`.
6. Create and save the backup password somewhere safe.
7. Click `Back Up Now`.

Apple notes that you can’t use the encrypted backup without that password, so keep it safe.

The default macOS backup root is:

- `~/Library/Application Support/MobileSync/Backup/`

### Step 2. Install and run one command

Clone the repo and install dependencies:

```bash
git clone https://github.com/crajarshi/whatsapp-local-exporter.git
cd whatsapp-local-exporter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This command investigates the backup, prints the size/count breakdown first, then prompts securely for the Finder backup password and exports into `~/Downloads/whatsapp-export`.

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --export \
  --password-prompt \
  --types all \
  --output ~/Downloads/whatsapp-export \
  --resume
```

What this currently exports by category:

- `video` to `~/Downloads/whatsapp-export/videos/`
- `image` to `~/Downloads/whatsapp-export/images/`
- `audio` to `~/Downloads/whatsapp-export/audio/`
- `document` to `~/Downloads/whatsapp-export/pdfs/`
- `chat` to `~/Downloads/whatsapp-export/chats/`
- `database` to `~/Downloads/whatsapp-export/databases/`
- `other` to `~/Downloads/whatsapp-export/other/`

The `chat` directory contains raw WhatsApp chat-related SQLite files such as `ChatStorage.sqlite`. It is not yet a rendered text transcript.
The `pdfs/` directory contains PDFs plus other document-style files such as `.docx` and `.xlsx`.

Why there is a dependency now:

- encrypted Finder backups do not expose a plaintext `Manifest.db`
- this project uses `iphone_backup_decrypt` to create a decrypted working copy of `Manifest.db` outside the source backup

## Troubleshooting

- If the backup lives under `~/Library/Application Support/MobileSync/Backup/`, give Full Disk Access to Codex and to Terminal or iTerm if that is what launches the CLI.
- If you are not sure which backup folder to use, run:

```bash
python cli.py --list-backups
```

- If you want to inspect first without exporting, run:

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --dry-run \
  --output ~/Downloads/whatsapp-export
```

- If you want the CLI to decrypt and inspect the manifest before exporting, use:

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --dry-run \
  --password-prompt \
  --output ~/Downloads/whatsapp-export \
  --verbose
```

- The password prompt is hidden and never uses `--password <value>`. If Codex Desktop’s embedded terminal does not accept hidden input cleanly, run the command in normal Terminal or iTerm instead.
- After any run, inspect `manifest.json`, `summary.txt`, and `unresolved.json` inside the output folder.

## CLI Flags

- `--list-backups`
  List candidate Finder backups.

- `--backup-path <path>`
  Use one exact Finder backup directory.

- `--scan`
  Inspect the selected backup structure.

- `--dry-run`
  Investigate only. No exports.

- `--export`
  Run the actual export after investigation and pre-export reporting.

- `--output <dir>`
  Output folder for `manifest.json`, `summary.txt`, `unresolved.json`, and state files.

- `--types all`
  Export all WhatsApp files discovered in the decrypted manifest.

- `--types video,image,audio,document,chat,database,other`
  Export only the selected categories. `pdf` is accepted as an alias for `document`.

- `--resume`
  Reuse prior export results and skip files already exported and hashed.

- `--verbose`
  Print the structured investigation payload.

- `--password-prompt`
  Prompt securely for the encrypted backup password.

## Important Notes

- The source backup is never modified.
- Decrypted working files are written outside the source backup.
- If the password is not supplied, the tool will stop honestly at the encrypted-manifest boundary.
- If decryption fails, the exact error is surfaced in the artifacts.
- Export success is proven at the manifest/blob level for videos and documents from the tested backup.
- Full readable chat reconstruction still requires WhatsApp database joins beyond the current manifest/path-based exporter.
