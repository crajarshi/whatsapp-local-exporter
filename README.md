# Finder Backup WhatsApp Investigator

This project is a macOS-first Python CLI for investigating an encrypted Finder iPhone backup and determining whether WhatsApp videos and PDFs/documents can be globally enumerated and extracted from it.

It is investigation-first on purpose:

- the backup is treated as read-only
- no WhatsApp Mac linked-device data is used
- no iCloud browsing is used
- no live iPhone app access is used
- no extraction success is claimed until real files are proven

## Current Status

The current code has been tested against a real encrypted Finder backup and can now do these parts:

- discover Finder backups
- inspect `Info.plist`, `Manifest.plist`, and `Status.plist`
- detect whether the backup is encrypted
- detect when raw `Manifest.db` is encrypted and not plaintext SQLite
- identify WhatsApp app/app-group presence from `Manifest.plist`
- decrypt a working copy of `Manifest.db` when you provide the backup password
- globally enumerate WhatsApp manifest records and classify export candidates
- print a pre-export size/count report before exporting
- decrypt and export WhatsApp backup files into output folders by category
- dedupe exported files by SHA-256
- write `manifest.json`, `summary.txt`, and `unresolved.json`

Verified results from the tested backup:

- `33770` WhatsApp manifest rows
- `27925` WhatsApp file records
- `136` manifest-level chats with media-path identifiers when running `--types all`
- `1216` video candidates
- `11938` image candidates
- `46` audio candidates
- `142` document candidates
- `5` raw chat-database candidates
- `23` additional SQLite database candidates
- `14555` other WhatsApp backup files
- `6.99 GiB` total WhatsApp file data in the tested backup
- `966` unique files exported
- `392` duplicate records preserved in the manifest
- `0` failed or unresolved export records

Important limitations:

- end-to-end blob export is directly proven for videos and documents from the tested backup
- images, audio, raw chat databases, databases, and `other` files are now classified and supported by the CLI, but they have not yet been re-run end to end in this exact session with a second secure export pass
- `chat_id` is often recoverable from media paths, but `chat_name`, `message_id`, and `sender` are still usually blank because `ChatStorage.sqlite` is not yet joined into the export manifest
- the `chat` export category currently means raw WhatsApp SQLite files, not a human-readable conversation transcript

## Project Files

- `cli.py`
- `backup_locator.py`
- `backup_manifest_parser.py`
- `backup_decryptor.py`
- `whatsapp_locator.py`
- `schema_inspector.py`
- `attachment_enumerator.py`
- `exporter.py`
- `dedupe.py`
- `manifest.py`
- `utils.py`
- `findings.md`
- `requirements.txt`

## Requirements

- macOS
- Python 3.9+
- a Finder iPhone backup already present on disk
- Full Disk Access for Codex/Terminal if needed

Default Finder backup root:

- `~/Library/Application Support/MobileSync/Backup/`

Default export output directory:

- `~/Downloads/whatsapp-export`

## Quick Start

### Step 1. Create the encrypted Finder backup

1. Connect the iPhone to your Mac with a cable.
2. Open Finder and select the iPhone in the sidebar.
3. In the General tab, choose `Back up all of the data on your iPhone to this Mac`.
4. Enable `Encrypt local backup`.
5. Click `Back Up Now`.
6. Wait for the backup to finish.

The default macOS backup root is:

- `~/Library/Application Support/MobileSync/Backup/`

### Step 2. Run one command

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

## Step By Step

### 1. Clone the repo

```bash
git clone https://github.com/crajarshi/whatsapp-local-exporter.git
cd whatsapp-local-exporter
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Why there is a dependency now:

- encrypted Finder backups do not expose a plaintext `Manifest.db`
- this project uses `iphone_backup_decrypt` to create a decrypted working copy of `Manifest.db` outside the source backup

### 4. Make sure macOS privacy is not blocking the backup

If the backup lives under `~/Library/Application Support/MobileSync/Backup/`, give Full Disk Access to:

- Codex
- Terminal or iTerm, if that is what launches the CLI

### 5. List available backups

```bash
python cli.py --list-backups
```

### 6. Run a dry-run without a password first

This confirms discovery, backup metadata, encryption state, and whether WhatsApp is visible in `Manifest.plist`.

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --dry-run \
  --output ./output \
  --verbose
```

Expected result for an encrypted backup:

- backup path is readable
- backup encrypted = yes
- WhatsApp data located = yes if WhatsApp app/app-group identifiers are present
- `Manifest.db` reported as encrypted or opaque on disk until a password is supplied

### 7. Run a dry-run with `--password-prompt`

This is the next real step for encrypted backups. The CLI will securely prompt for the Finder backup password without echoing it.

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --dry-run \
  --password-prompt \
  --output ./output \
  --verbose
```

If you are using Codex Desktop and the embedded terminal does not accept hidden password input cleanly, run the same command in your normal macOS Terminal or iTerm window instead.

If the password is correct, the tool will:

- decrypt a working copy of `Manifest.db`
- store that working copy under `output/.state/<backup-id>/`
- keep the original backup untouched
- continue investigation from the decrypted manifest copy

There is intentionally no `--password <value>` flag, because that would leak into shell history and process lists.

Optional secondary mode:

- if you really need non-interactive execution, set `FINDER_BACKUP_PASSWORD` in the environment
- the CLI never writes the password value to stdout, stderr, manifests, or summary files

### 8. Review the generated artifacts

After a run, inspect:

- `output/manifest.json`
- `output/summary.txt`
- `output/unresolved.json`

These files tell you:

- which backup was selected
- whether it is encrypted
- whether decryption succeeded
- whether WhatsApp presence was proven
- whether `Manifest.db` was directly readable or required decryption
- what is still unresolved

### 9. Run the actual export

The CLI now prints a pre-export report first, including:

- total WhatsApp file bytes
- total WhatsApp media file bytes
- target export bytes
- target video count and bytes
- target image count and bytes when selected
- target audio count and bytes when selected
- target document count and bytes
- target raw chat/database count and bytes when selected
- manifest-level target chat count

Then it decrypts and exports the selected file types.

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --export \
  --password-prompt \
  --output ~/Downloads/whatsapp-export \
  --types all \
  --resume
```

Export output locations:

- `output/videos/`
- `output/images/`
- `output/audio/`
- `output/pdfs/`
- `output/chats/`
- `output/databases/`
- `output/other/`

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
