# goco — automated invoice collection

Windows-first CLI for original invoice attachments from Gmail and manual PDF/XML/email imports. The first implementation is available for local testing. Live company Gmail and shared-storage acceptance remain pending.

## Start

Run these commands from the project folder in PowerShell.

```powershell
.\.venv\Scripts\python.exe -m goco --help
.\.venv\Scripts\python.exe -m goco --config config/example.json config validate
.\.venv\Scripts\python.exe -m goco --config config/example.json list-services
.\.venv\Scripts\python.exe -m pytest -q
```

The example configuration is synthetic and uses manual collection. It does not connect a mailbox or import real documents. Copy it into `private/config.json` and adjust relative storage paths before configuring company accounts. Paths are resolved relative to the configuration file.

For a fresh environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

## What works

- Strict JSON configuration with owners, billing accounts, mailbox references, sender rules and explicit invoice obligations.
- Original-file import, SHA-256 duplicate detection, preserved corrected versions and metadata review history.
- Read-only Gmail OAuth with Windows credential storage, pagination, nested MIME attachments and account ambiguity handling.
- Text/JSON status, portable reports, interrupted-run records and SQLite index rebuild from original files/sidecars.
- Dry-run Gmail discovery without attachment-body downloads, writes or token refresh.

An attachment can be saved but still require metadata review. Receipt, invoice, statement and email source are distinct records. Receiving an email in October does not establish an October invoice service period.

## Guides

- [Setup and operations](docs/operations.md)
- [Company pilot checklist](docs/pilot-checklist.md)
- [Implementation specification](docs/superpowers/specs/2026-11-05-goco-mvp-design.md)
- [Implementation plan](docs/superpowers/plans/2026-11-05-goco-mvp.md)
- [Implementation progress and rulings](docs/implementation-progress.md)
- [Vendor research](docs/vendor-feasibility.md)
- [Original project brief](docs/project-brief.txt)

AWS API collection is the next release. Browser automation, automatic email-link downloads, OCR, scheduling and direct Drive/SharePoint uploads are outside this release. The tool does not send email, modify Gmail messages or configure shared-folder permissions.
