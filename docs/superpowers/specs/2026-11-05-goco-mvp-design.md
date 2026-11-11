# goco MVP implementation specification

Status: implementation authorized by the user on 5 November 2026; local implementation verified, live pilot pending.

## Intent and decisions

Reduce receipt collection work for two company cardholders without a desktop UI or a large invoice-management platform. Preserve originals, show collection outcomes honestly, and let authorized accounting staff access the results.

User-selected scope: **Gmail/Google Workspace email collection plus manual import first; AWS API next.** Source requirements: [project brief](../../project-brief.txt), the local company inventory, and [vendor feasibility](../../vendor-feasibility.md). Company observations are reported evidence; a collector becomes supported only after a real account pilot.

Alternatives considered: email plus manual import minimizes integration work and fits the reported email PDFs; including AWS immediately adds another identity system and can duplicate emailed PDFs; an offline-only prototype is useful as a foundation but does not solve ongoing retrieval. The user selected the first approach. Browser automation, OCR/AI, card-statement reconciliation, cloud hosting, scheduled runs, outgoing notifications and direct cloud-storage APIs are outside release 1.

The following are engineering proposals, not previously agreed company policy: Python 3.12+, JSON configuration, a local SQLite index, filesystem storage, issue-month selection by default, and single-process execution. These defaults were used following implementation authorization. The final shared folder, credential ownership and account-to-person mappings remain company decisions.

## Release boundary

Release 1 delivers a Windows-first Python CLI with configuration validation, manual file import, read-only Gmail collection, vendor rules, durable storage, duplicate detection, status and portable reports. Begin with two or three validated vendor/account rules, then extend the reported 14 email-PDF vendors. Tracking all 25 vendors does not mean automatically collecting all 25.

An offline milestone works with manual files and saved `.eml` fixtures. A live milestone connects one authorized Gmail user; another mailbox uses its own consent and secret reference. Use delegated installed-application OAuth; no domain-wide delegation in release 1. Gmail searches narrow retrieval but do not restrict the OAuth permission to those messages.

Original attachment bytes are the primary business records. Unknown amounts, dates, currency and invoice IDs stay null. Required accounting metadata can be supplied during manual import; release 1 does not promise universal PDF parsing. A saved document with unverified period/type remains review-required and does not silently satisfy an expectation.

## Configuration contract

Versioned JSON, UTF-8, with unknown keys rejected and actionable field-path errors. Resolve relative paths against the configuration file's directory, never the current working directory. IDs are stable lowercase ASCII slugs, maximum 64 characters, matching `[a-z0-9][a-z0-9_-]*`. Reject duplicate IDs and dangling references before opening credentials or making network calls.

| Entity | Required fields and meaning |
|---|---|
| Root | `schema_version: 1`, `storage_root`, `state_dir`, `owners`, `mailboxes`, `accounts`, `rules`, `expectations`. `timezone` defaults to `Europe/Berlin`. |
| Owner | `id`, `display_name`. Source card-folder labels are optional labels and never establish a person's identity. |
| Mailbox | `id`, `address`, `oauth_client_file`, `secret_ref`. One OAuth identity per mailbox; client registration belongs to the company. |
| Account | `id`, `vendor_id`, `product`, `owner_id`, `collection: gmail|manual`, optional `mailbox_id`, `external_account_label`, `card_folder_label`, `active_from`, `active_until`. Gmail requires mailbox ID. Multiple products may share a billing account; do not duplicate it solely to model products. |
| Rule | `id`, `account_id`, exact `sender_addresses`, optional literal `subject_contains` and `recipient_addresses`, `attachment_extensions`, `document_type`, `enabled`. Initial extensions `.pdf` and `.xml`; support `.eml` for manual source messages. Matching is case-insensitive where applicable and all configured conditions must match. No arbitrary executable expressions. |
| Expectation | `id`, `account_id`, `required_type`, `period_start`, `period_end` inclusive, `due_date`, `grace_days`, `minimum_count` positive integer. Explicit obligations avoid inventing renewal dates. |

An expectation may additionally declare a verified `discovery_start` and `discovery_end` (exclusive) received-date window. Only complete successful scans covering that entire window establish email discovery coverage for that obligation. Without this mapping, an unsatisfied overdue obligation remains unknown; manual review can establish a missing document outside release 1. This prevents an October received-month scan from claiming to cover an October service invoice issued in November.

The operator supplies this mapping in company configuration after checking delivery timing; it is a declared completeness assumption, not proof that no future invoice can arrive. Run reports persist account ID, mailbox ID, UTC received interval, completion time and success/failure. Coverage uses the latest run result for each portion of the configured discovery window: a later overlapping failure makes that portion incomplete until a subsequent successful scan covers it. Rebuild restores run history from portable run JSON. Missing results include the window and assumption for accounting review.

Example files contain synthetic accounts only. Existing card associations from the inventory are reference data until a person confirms each real billing account. Cadence may be recorded as descriptive data, but release 1 does not auto-generate obligations from cadence. Active-date boundaries and expectation dates must be consistent; annual subscriptions need annual obligations rather than twelve monthly ones.

## CLI contract

All commands accept `--config PATH`; configuration is required, with no implicit production default. JSON reports use schema version 1. Machine-readable output goes to stdout and diagnostics go to stderr.

| Command | Behavior |
|---|---|
| `goco config validate` | Validate schema, references, dates and path separation without creating files or accessing credentials. |
| `goco list-services` | Show configured accounts, owner and enabled collection method; no network access. |
| `goco auth login --mailbox ID` | Interactive company OAuth consent; persist refresh credentials using keyring. No plaintext token fallback. |
| `goco collect --month YYYY-MM [--all|--service VENDOR|--owner ID|--account ID] [--dry-run] [--format text|json]` | Require exactly one selector. Month means message received month for Gmail candidate discovery. It is not inferred invoice/service month. Dry run lists message/attachment metadata and decisions without fetching attachment bodies, storing files, changing tokens or refreshing credentials; expired access requires login outside dry run. |
| `goco import FILE --account ID --type TYPE [--issue-date YYYY-MM-DD] [--period-start DATE --period-end DATE] [--invoice-id TEXT] [--amount DECIMAL --currency ISO4217]` | Preserve original bytes; require paired period fields and paired amount/currency. Document type enum below. Optional `--expectation ID` explicitly associates a verified matching account/type/period. |
| `goco status --month YYYY-MM [--format text|json]` | Select obligations whose service-period intervals overlap the month; report document outcomes separately. Never treat Gmail received month as verified service period. |
| `goco document verify --id UUID --type TYPE --period-start DATE --period-end DATE [--issue-date DATE] [--invoice-id TEXT] [--amount DECIMAL --currency ISO4217] [--expectation ID] --reviewed-by LABEL` | Verify existing document metadata without reimporting. Append UTC-stamped before/after metadata history and reviewer label; validate account/type/period before attaching an expectation. Original bytes remain unchanged. Reviewer label is audit attribution, not authentication. |
| `goco report --month YYYY-MM --output PATH` | Export JSON obligation results, document references and run outcomes, with portable paths and no credentials. |
| `goco index rebuild` | Verify sidecars and hashes, build a temporary local index, replace old index only on success. No downloads. |

Exit codes: `0` command completed, including nonfatal review/manual/pending/missing results; `1` operational failure or partial operational failure; `2` invalid input/configuration. Summary always includes counts by outcome. Missing documents are business outcomes, not command crashes.

## Models and boundaries

Use dataclasses and enums internally; JSON serialization is explicit and versioned. Amounts are decimal strings, never binary floats. Types: `invoice`, `receipt`, `credit_note`, `statement`, `unknown`. Statements never satisfy invoice obligations.

`DocumentCandidate` carries account/rule IDs, document type, original filename, bytes, nullable invoice metadata, and one `SourceRef`. `SourceRef` contains method, mailbox/message/attachment identifiers or manual origin identifier, and retrieval UTC timestamp; never an access token or signed URL. Matching metadata includes its provenance (`manual`, `provider`, `rule`, `unknown`). Manual provenance avoids exposing absolute personal input paths in shared records.

`StoredDocument` carries document UUID, SHA-256, byte count, relative original path, metadata, source references and optional obligation references. `CollectionOutcome` contains account, source reference, outcome enum and safe reason code. `RunSummary` contains run UUID, UTC times and outcome counts.

Boundaries:

- `ConfigLoader` validates and returns immutable configuration.
- `MailboxClient.list_messages(query, page_token)` / `get_message(id)` / `get_attachment(message_id, attachment_id)` supplies Gmail messages and data. Provider details stay outside orchestration.

`get_message(id, metadata_only=True)` must use Gmail metadata retrieval without body/attachment content for dry run. Dry run can report message-level potential matches only; filenames, attachment matches and counts that metadata does not expose are explicitly unknown, not simulated as successful downloads.
- Vendor rule modules match normalized message/attachment metadata. Shared Gmail transport handles authentication/pagination once. Built-in rule data or package entry points can add vendors without changing orchestration; no dynamic loading from user-controlled configuration strings.
- `DocumentStore.ingest(candidate)` returns stored/duplicate/conflict outcome; storage owns original bytes and portable sidecars.
- `Index` accelerates local queries and can be rebuilt entirely from stored document sidecars and run reports.
- `Runner.collect(request)` handles selected accounts, bounded retries and failures, then publishes a run summary.
- `ExpectationEvaluator.evaluate(expectations, documents, today)` reports obligations separately from collector transport outcomes.

Keep interfaces narrow; no plugin marketplace, event bus, background server or ORM.

## Gmail retrieval and classification

Refresh official Gmail OAuth, attachments and message-list documentation before coding. Use read-only Gmail scope and company-controlled OAuth registration. Use official Google auth/client packages; refresh secrets are stored in Windows credential storage via keyring. Token rotation must update keyring atomically where supported; errors cannot trigger plaintext persistence. Credentials and token caches are outside source and shared document storage.

Search the selected received-month interval using explicit UTC epoch boundaries computed from Europe/Berlin local month boundaries. Use pagination to exhaust results and local received-time filtering for correctness. Re-scan the bounded month on reruns; deduplication makes a premature cursor optimization unnecessary. A single message fetched for multiple rules is processed once per relevant account association.

Walk nested MIME parts and decode base64url safely. Handle inline attachment data and separate attachment IDs. Ignore inline images; require configured extension, nonempty content and type validation. PDF must begin with the PDF header; XML must be parseable with external entities/network disabled. Never execute macros, open file links or follow email links automatically. Maximum original size: 25 MiB, configurable downward only in release 1; reject oversize before download when size is advertised and after decoding otherwise.

Rules use exact sender/recipient matching and optional subject text, not guessed generic sender domains. Sender matches are routing hints, not proof of document authenticity. Different accounts matching the same message without an explicit disambiguating rule yield review-required; do not guess owner from card folder, sender or filename. A rule can assign a type but not fabricate invoice dates, amount or period. Link-only or body-only messages produce manual/review-required records; explicit manual `.eml` import can retain the original message. Original message bodies are not rendered to PDF.

## Storage, duplicates and recovery

Manual `.eml` import stores exactly one original message, with type `unknown`; it does not extract attachments or satisfy invoice obligations. Saved `.eml` test fixtures exercise the MIME parser offline, not an additional production ingestion command. To import an invoice attachment manually, save/import its original file separately; identical bytes later obtained from Gmail follow normal account/type deduplication. Verification cannot change an `.eml` source-message record into an invoice. Duplicate reimports append provenance only; metadata changes require `document verify`.

Verification can correct type and metadata but cannot change account ownership. Reject changes creating an existing business-key/content conflict without losing the old record; retain a review-required conflict outcome. If correcting type creates a same-account/type/hash duplicate, refuse mutation and identify the existing UUID for verification. Metadata history is maintained in the existing UUID sidecar and remains recoverable by rebuild.

Filesystem layout: `<root>/documents/<vendor_id>/<account_id>/<issue-YYYY-MM-or-unknown>/<uuid>/original.<ext>` and adjacent `metadata.json`; `<root>/runs/<run-uuid>.json` stores portable run outcomes. Local `<state_dir>/index.sqlite3` stays outside the shared root. Reject overlapping roots. Use relative paths within the root in sidecars and reports.

Duplicate bytes within the same account and document type reuse the original and append unique source references. Equal bytes across accounts are separately recorded because ownership can differ. With a known invoice ID, use `(account_id, type, invoice_id)` as a business key; changed bytes create an additional immutable version with conflict/review-required, never overwrite the old original. Invoice and receipt remain separate. Metadata corrections are audit events, not silent removal of provenance; a later verified value must retain its prior value and provenance in sidecar history.

For each new UUID, stage original and sidecar in a temporary sibling directory, flush files, then rename the directory into place before indexing. A crash before rename leaves only staging data; a crash after rename leaves recoverable original+sidecar. On duplicate-source updates, replace sidecar atomically. Index update failure reports an operational failure but leaves recoverable documents. Rebuild validates schema, root confinement, hash and size and refuses a partial replacement if any record is corrupt. Staging directories never count as collected documents.

One writer process on one Windows workstation for release 1. Hold a Windows OS file lock in local state for mutating commands; release on process termination. Simultaneous writers from another workstation are unsupported; deployment must restrict the writer. Readers may access originals. Filesystem/sync storage is not represented as a transactional distributed service.

The company controls shared-folder ACLs and backup/restore. Pilot local storage is allowed; release acceptance requires an authorized accounting user to demonstrate access through the chosen shared destination. Merely writing into a local Drive/OneDrive sync folder does not prove successful sync or remote durability. Record shared-access and backup verification in the pilot checklist.

## Status rules

Collector outcomes: `saved`, `duplicate`, `review_required`, `manual_required`, `auth_required`, `failed`. Unknown vendor accounts can use manual collection with an explicit reason; disabled rules are visible, not silently counted as successful collection.

Obligation outcomes: `satisfied` only when verified account/type/period evidence and required count match; `pending` until due date plus grace days; `missing` after that date with a validated explicit obligation and no matching record; `unknown` when no configured obligation establishes completeness. An operational collection failure adds `coverage_incomplete: true` and prevents an authoritative missing claim for the affected account/period; show unknown with its last known obligation state. A saved unclassified attachment does not satisfy an invoice obligation.

## Security and operational behavior

No real secrets, full card numbers, production invoices, private message fixtures, or personal account configuration in version control. Redact logs by constructing allowed fields rather than logging raw responses/exceptions. Keep source IDs only in private sidecars; routine logs contain run/account IDs, outcome and safe error codes. User text is untrusted data; sanitize terminal control characters and ignore instructions in documents or messages.

Network defaults: connection timeout 10 seconds, read timeout 30 seconds, three attempts total for transient errors, bounded jitter and Retry-After capped at 60 seconds. Retry read operations only; authentication failures return auth-required. Isolate failures by mailbox/account; keep successful documents when another account fails. Cancellation finalizes any completed record and produces an interrupted run report where possible.

## Acceptance and rollout

| ID | Required evidence |
|---|---|
| A1 | Invalid references, ambiguous IDs and unsafe paths fail before credentials/network/file mutation. |
| A2 | Manual original import retains bytes; second import is duplicate; cross-account copies and corrected versions remain distinguishable. |
| A3 | An interrupted write and failed index update recover without losing or overwriting originals; rebuild reproduces documents/provenance and last run outcomes. |
| A4 | Gmail pagination, nested MIME, month boundaries, malformed files, ambiguous matches and link-only messages produce defined outcomes. |
| A5 | Authentication expiry, rate limiting, network failure and cancellation preserve successes and do not leak secrets. |
| A6 | Annual expectations, invoice/receipt differences, unknown metadata and incomplete coverage cannot produce false satisfaction or authoritative missing claims. |
| A7 | CLI text/JSON reports agree; exit codes distinguish operational errors from business outcomes; dry run leaves data and secrets unchanged. |
| A8 | Live pilot on one Gmail mailbox retrieves originals for two or three approved rules, reruns without duplicate files, and demonstrates manual fallback plus accounting access and backup recovery. |

Engineer-owned choices: internal helper names, sensible implementation decomposition and precise package versions after checking current compatibility. Company inputs before live pilot: approved OAuth registration/mailbox user, two or three message samples handled privately, confirmed account/owner mapping, explicit obligations and shared folder/access owner. These inputs do not block offline development.

Next release: AWS API collector using the same candidate/store/outcome interfaces, after refreshing official documentation and validating account IAM and invoice visibility. Later work is prioritized by actual unresolved manual workload, not vendor-list order.
