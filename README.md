# WhatsApp Local Exporter

This project exports locally available WhatsApp macOS videos and PDFs by reading WhatsApp's on-disk storage directly. It does not use iPhone backups, and it does not require per-chat manual export.

## Requirements

- macOS
- WhatsApp for Mac installed and signed in
- Python 3.9 or newer
- the target videos and PDFs must already exist locally in WhatsApp's storage

## What This Tool Does

- discovers WhatsApp containers and group containers
- inspects the local WhatsApp SQLite stores
- globally enumerates locally available video and PDF attachments across all chats
- exports those files into a clean output directory
- computes SHA-256 hashes
- dedupes exported files by content hash
- keeps duplicate and failure records in `manifest.json`

## What It Uses Internally

Primary path:

- `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`
- `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message/Media/...`

The exporter reads `ZWAMESSAGE`, `ZWAMEDIAITEM`, `ZWACHATSESSION`, and `ZWAGROUPMEMBER` from `ChatStorage.sqlite`, then resolves WhatsApp's stored relative media paths to real files on disk.

## What It Produces

Inside the chosen `--output` directory:

- `manifest.json`
- `summary.txt`
- `unresolved.json`
- `videos/`
- `pdfs/`

Each manifest record includes:

- `chat_id`
- `chat_name`
- `message_id`
- `sender`
- `timestamp`
- `attachment_type`
- `mime_type`
- `original_filename`
- `source_local_path`
- `exported_path`
- `file_size`
- `sha256`
- `status`
- `notes`

## Step By Step

### 1. Clone The Repo

```bash
git clone https://github.com/crajarshi/whatsapp-local-exporter.git
cd whatsapp-local-exporter
```

If you use a different repository name, replace the URL and folder name accordingly.

### 2. Confirm Python Is Available

```bash
python3 -V
```

### 3. Run A Storage Scan

This prints the WhatsApp directories and databases the tool can see on your Mac.

```bash
python3 -m whatsapp_local_exporter --scan
```

### 4. Run A Dry-Run First

This is the safest first pass. It discovers all matching records globally without copying files.

```bash
python3 -m whatsapp_local_exporter \
  --scan \
  --dry-run \
  --types video,pdf \
  --output ./dryrun
```

After this completes, inspect:

- `./dryrun/manifest.json`
- `./dryrun/summary.txt`
- `./dryrun/unresolved.json`

### 5. Run The Real Export

This copies locally available files into `./output/videos` and `./output/pdfs`.

```bash
python3 -m whatsapp_local_exporter \
  --export \
  --types video,pdf \
  --output ./output
```

### 6. Resume If Needed

If the export stops mid-run, rerun it with `--resume`.

```bash
python3 -m whatsapp_local_exporter \
  --export \
  --resume \
  --types video,pdf \
  --output ./output
```

## CLI Flags

Scan only:

```bash
python3 -m whatsapp_local_exporter --scan
```

- `--scan`
  Discover WhatsApp storage locations and print what was found.

- `--dry-run`
  Enumerate records without copying files.

- `--export`
  Copy matching files into the output directory.

- `--output <dir>`
  Choose where manifests and exported files go.

- `--types video,pdf`
  Limit attachment types. Supported values are `video` and `pdf`.

- `--resume`
  Reuse an existing `manifest.json` in the output directory.

- `--verbose`
  Print extra details, including the scan and run summary JSON.

## Common Commands

Global dry-run across all chats:

```bash
python3 -m whatsapp_local_exporter \
  --scan \
  --dry-run \
  --types video,pdf \
  --output ./dryrun
```

Real export:

```bash
python3 -m whatsapp_local_exporter \
  --export \
  --types video,pdf \
  --output ./output
```

Resume a previous export run:

```bash
python3 -m whatsapp_local_exporter \
  --export \
  --resume \
  --types video,pdf \
  --output ./output
```

Install as an editable local CLI if you prefer the entrypoint name:

```bash
python3 -m pip install -e .
whatsapp-local-exporter --scan
```

## Output Layout

After a successful export, you will typically have:

- `output/manifest.json`
- `output/summary.txt`
- `output/unresolved.json`
- `output/videos/`
- `output/pdfs/`

`manifest.json` contains one record per matching WhatsApp message attachment, even when multiple records point to the same exported file after dedupe.

## Notes

- Export dedupes by SHA-256 and keeps duplicate records in the manifest.
- `original_filename` is best-effort. On the observed WhatsApp schema, most rows do not expose a stable original filename, so the exporter usually falls back to the local UUID-style basename.
- The database stores relative paths like `Media/...`, but on the tested native WhatsApp build the real files live under `Message/Media/...`. The exporter resolves that mapping automatically.
- Only files already present on disk are exported.
- If WhatsApp has metadata for a message but the file is not currently cached on disk, the tool will not download it.
- On some Macs, Terminal may need permission to read `~/Library/Containers` and `~/Library/Group Containers`.

## Fallback UI Mode

`fallback_ui.py` is included as a separate brittle secondary mode. It is not the primary workflow and was not needed on the tested machine because direct parsing succeeded.

Example:

```bash
python3 fallback_ui.py --chat-name "Example Chat" --dry-run
```

This script only provides a thin AppleScript-driven starting point for UI search automation and should be treated as experimental.
