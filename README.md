# Finder Backup WhatsApp Investigator

This project is a macOS-first Python CLI for investigating an encrypted Finder iPhone backup and determining whether WhatsApp videos and PDFs/documents can be globally enumerated and extracted from it.

It is investigation-first on purpose:

- the backup is treated as read-only
- no WhatsApp Mac linked-device data is used
- no iCloud browsing is used
- no live iPhone app access is used
- no extraction success is claimed until real files are proven

## Current Status

The current code has been tested against a real encrypted Finder backup and can now do these parts end to end:

- discover Finder backups
- inspect `Info.plist`, `Manifest.plist`, and `Status.plist`
- detect whether the backup is encrypted
- detect when raw `Manifest.db` is encrypted and not plaintext SQLite
- identify WhatsApp app/app-group presence from `Manifest.plist`
- decrypt a working copy of `Manifest.db` when you provide the backup password
- globally enumerate WhatsApp manifest records and target `video,pdf` candidates
- print a pre-export size/count report before exporting
- decrypt and export WhatsApp videos and PDFs/documents into output folders
- dedupe exported files by SHA-256
- write `manifest.json`, `summary.txt`, and `unresolved.json`

Verified results from the tested backup:

- `33770` WhatsApp manifest rows
- `52` manifest-level chats with target attachments
- `1216` video candidates
- `142` PDF/document candidates
- `2.64 GiB` targeted export data
- `966` unique files exported
- `392` duplicate records preserved in the manifest
- `0` failed or unresolved export records

Important limitation:

- export works from Finder-backup manifest data and decrypted file blobs
- `chat_id` is often recoverable from media paths, but `chat_name`, `message_id`, and `sender` are still usually blank because `ChatStorage.sqlite` is not yet joined into the export manifest

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
- target PDF/document count and bytes
- manifest-level target chat count

Then it decrypts and exports the selected file types.

```bash
python cli.py \
  --backup-path "/Users/<you>/Library/Application Support/MobileSync/Backup/<backup-id>" \
  --export \
  --password-prompt \
  --output ./output \
  --types video,pdf \
  --resume
```

Export output locations:

- `output/videos/`
- `output/pdfs/`

The `pdfs/` folder contains both PDFs and other document-style exports such as `.docx` when they match the requested `pdf`/document category.

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

- `--types video,pdf`
  Target attachment categories. `video,pdf` exports videos plus PDFs/documents.

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
- Export success is proven at the manifest/blob level for the tested backup, but rich chat/message metadata is still partial until WhatsApp message databases are joined.
