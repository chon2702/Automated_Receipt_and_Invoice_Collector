# Setup and operations

## Deployment boundary

Use Python 3.12 or newer on one Windows workstation. Keep the program, local state and OAuth client registration on that workstation; use an approved shared folder only for original documents, sidecars and portable run records. A local-only folder works for testing but does not meet accounting access requirements.

One workstation writes; accounting users may read. The writer lock protects the local state directory, not multiple PCs or distributed cloud sync. Configure folder permissions and backup externally. Confirm remote sync separately if using a Drive/OneDrive sync folder. This collector is not a legal archiving or retention-policy system.

The environment in the project directory has been installed. `requirements-lock.txt` records versions used for this implementation's local tests, including test dependencies. Recheck current supported dependencies before future upgrades. Use `python -m pip` from `.venv` rather than a global pip command.

## Configuration

Copy `config/example.json` to `private/config.json`. Its paths are relative to the file: for example `storage_root: "documents"` and `state_dir: "state"` inside `private` are separate siblings. No state or shared-storage paths may overlap. Production configuration, credentials and invoice samples must stay out of version control.

Each account needs stable IDs, an explicit owner, vendor, product and collection method. Card-folder labels from the inventory are labels, not verified owner identities. Separate Google Workspace, Google Ads, Cloud/Gemini and Microsoft/LinkedIn billing products according to actual billing accounts. Keep annual subscriptions annual. Begin with manual accounts, then enable Gmail accounts once their product name and search aliases have been checked against a dry run.

All root fields are required except timezone (`Europe/Berlin`), `max_bytes` (maximum 26214400) and `account_defaults`. `account_defaults` can supply `owner_id`, `collection` and `mailbox_id` for every account. Values written in an account win, and the default mailbox applies only to accounts whose resolved collection is `gmail`. Accounts are validated after the defaults are filled in. For the compact per-manager template, see [Gmail test for the VA and VL managers](manager-gmail-setup.md). Entity IDs are lowercase slugs, maximum 64 characters. Unknown fields are rejected. Refer to the specification for exact fields; the application gives a field path for invalid configuration.

A synthetic Gmail configuration fragment (add these entries to the existing arrays):

```json
{
  "mailboxes": [{
    "id": "billing-mail",
    "address": "billing@example.test",
    "oauth_client_file": "oauth-client.json",
    "secret_ref": "goco-billing-mail"
  }],
  "accounts": [{
    "id": "example-subscription",
    "vendor_id": "example-vendor",
    "product": "Example subscription",
    "owner_id": "person1",
    "collection": "gmail",
    "mailbox_id": "billing-mail",
    "search_aliases": ["Example Vendor"]
  }]
}
```

This is a fragment, not a complete config. Gmail accounts find invoice PDFs by `product` and `search_aliases`; see [Gmail PDF search by vendor name](gmail-pdf-search.md) for matching, review cases and the migration of old sender rules. Configure all vendors of a mailbox so vendor ambiguity can be detected, even when selecting just one account. Names route PDFs; they do not prove authenticity. Rules remain only for portal link notifications and need `"purpose": "link_notice"`.

## Company Gmail authorization

1. Company administrator registers an internal installed/desktop OAuth client in an approved Google Cloud project, enables Gmail API and configures allowed users/consent according to company policy. External/testing registrations can have different token lifetimes; confirm before scheduling anything.
2. Keep the downloaded desktop client JSON outside shared document storage and outside version control; reference its path in the mailbox config. Do not paste tokens or client secrets into chat.
3. Validate configuration, then run `goco --config private/config.json auth login --mailbox billing-mail` using the local `.venv` executable. The command opens normal browser consent and verifies the actual Gmail profile against the configured address.
4. Refresh credentials stay in Windows Credential Manager under service `goco-autoinvoices` and the configured `secret_ref`. There is no file-token fallback. Each mailbox uses a distinct reference. Do not reuse references for different users.

The scope is `gmail.readonly`; it reads the authorized mailbox, not just selected messages. No domain-wide delegation. The program never marks messages read, moves/deletes messages or sends mail. An expired/revoked login produces `auth_required`. Repeat explicit login when consent or permissions need repair. To revoke access, use the Google account's connected-app controls and remove the matching Windows credential entry; the tool does not revoke grants automatically.

Transport uses google-auth and installed-app OAuth with requests against documented Gmail endpoints. Requests use `(10, 30)` connect/read seconds, three attempts maximum for transient reads, bounded waits, and no automatic credential refresh during dry run. No signed email links are followed.

Official references checked for this implementation on 5 November 2026:

- [Gmail Python quickstart and setup](https://developers.google.com/workspace/gmail/api/quickstart/python)
- [OAuth scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Message listing and pagination](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
- [Message retrieval and metadata mode](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
- [Attachment retrieval](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments/get)
- [google-auth requests transport](https://google-auth.readthedocs.io/en/latest/reference/google.auth.transport.requests.html)

## Collect and import

Commands below assume `goco` means `.\.venv\Scripts\goco.exe`. `--config` can appear before or after the subcommand.

```powershell
goco --config private/config.json collect --all --month 2026-10 --dry-run --format json
goco --config private/config.json collect --owner person1 --month 2026-10
goco --config private/config.json collect --account example-subscription --month 2026-10
goco --config private/config.json import C:\Invoices\invoice.pdf --account example-subscription --type invoice --period-start 2026-10-01 --period-end 2026-10-31 --issue-date 2026-11-03 --invoice-id INV-123 --amount 49.00 --currency EUR
```

Exactly one collect selector is required (`--all`, `--service`, `--owner`, `--account`). Selected month is message receipt time in the configured timezone. November-issued invoices for October service may arrive in November; scan both months when applicable. Reruns rescan and deduplicate.

For link-notice rules, dry run retrieves message metadata only and shows potential message-level matches. For name-searched accounts it also reads message structure and text and reports the per-PDF decision; attachment contents are never downloaded. It does not create data/state, refresh tokens or save run reports. If the access token is expired, log in outside dry run first.

PDF/XML originals are preserved unchanged. File content has basic type/security checks, not full invoice authenticity or accounting validation. Maximum file size is 25 MiB and can be lowered by configuration. `.eml` can be manually preserved only with `--type unknown`; the message does not become an invoice or automatically extract its attachments. Import an actual attachment separately when needed.

## Review and obligations

Collection outcomes: `saved`, `duplicate`, `review_required`, `manual_required`, `skipped`, `auth_required`, `failed`. Text output starts with one line per selected account (saved, duplicates, review cases, skipped) and separates `ok, no matches` from a failed retrieval. A Gmail attachment normally requires metadata review because the app does not guess invoice dates, service periods or amounts. `status --format json` and text status list document IDs and relative paths.

```powershell
goco --config private/config.json document verify --id DOCUMENT-UUID --type invoice --period-start 2026-10-01 --period-end 2026-10-31 --reviewed-by Accounting
```

Add `--issue-date`, `--invoice-id`, paired `--amount`/`--currency`, or `--expectation ID` if known. Verification updates the sidecar with before/after metadata and reviewer label, retaining original bytes. Account ownership cannot be reassigned. Business-key or type-change duplicate conflicts return review-required; inspect records rather than deleting originals. An initial conflict version remains excluded from automatic satisfaction until resolved by a later supported workflow; release 1 does not silently choose a corrected invoice version.

For link-only, ambiguous or invalid messages, collection prints a safe `review_id`. Open `review/<review_id>.json` in the private storage root to find the mailbox/message/attachment IDs and locate the source in Gmail. These identifiers are kept out of routine collection logs and exported run summaries. Restrict review records to the same authorized staff as original documents.

Configure explicit obligations rather than monthly guesses:

```json
{
  "id": "example-october-invoice",
  "account_id": "example-subscription",
  "required_type": "invoice",
  "period_start": "2026-10-01",
  "period_end": "2026-10-31",
  "due_date": "2026-11-05",
  "grace_days": 3,
  "minimum_count": 1,
  "discovery_start": "2026-11-01",
  "discovery_end": "2026-12-01"
}
```

The received-date discovery interval is optional, end-exclusive, and must reflect a delivery window confirmed by the operator. It is an explicit assumption about completeness. Missing can be declared only after the grace date and successful complete scan coverage of that window, with no unresolved candidate. Only elapsed received days can be certified; a scan during November does not certify all of November. Later overlapping scan failures or disabled-rule runs invalidate that portion until rescanned successfully. Without a verified discovery window, an unsatisfied overdue obligation remains unknown. Known annual terms become annual obligations; archive date ranges alone do not establish ongoing activity.

`status --month` selects obligations by overlapping service period. `satisfied` requires verified account/type/period metadata and sufficient distinct documents. `pending`, `unknown` and `missing` remain separate. A bank statement does not satisfy an invoice obligation. No obligation means completeness is unknown. Exact service-period matching is intentionally conservative; manually verify dates consistent with the actual obligation.

## Reports and recovery

```powershell
goco --config private/config.json status --month 2026-10 --format json
goco --config private/config.json report --month 2026-10 --output private/reports/october-2026.json
goco --config private/config.json index rebuild
```

Reports include selected obligations and the full current document inventory/run summaries, including unresolved documents that cannot safely be assigned to a month. Output must be a new path outside authoritative documents/runs/local-state paths. Reports omit source message IDs and secrets; private sidecars retain provenance.

Originals live under `documents/vendor/account/issue-month-or-unknown/uuid/original.ext`, beside `metadata.json`. Metadata verification does not move originals when an issue date becomes known. Runs live under `runs/uuid.json`. Keep all of them in backup, including `review/uuid.json` private review-source records. SQLite is a local cache; `index rebuild` verifies original hashes/sizes and sidecars, builds a temporary index and replaces the old cache only after success. Corruption stops rebuild and leaves the existing cache untouched. Status reads authoritative sidecars, so a published original survives an index-write error.

Staging directories beginning `.stage-` are incomplete and excluded. Investigate them after an interrupted write; do not count or rename them into final records manually. The app leaves them for recovery investigation rather than deleting potentially useful bytes. Restore damaged originals/sidecars from backup and rerun rebuild. Never delete a production index or records as a first troubleshooting step.

A same-account/type/hash duplicate reuses its document UUID and retains unique sources. Equal bytes across accounts remain distinct ownership records. Changed bytes for an existing account/type/invoice ID are preserved as an additional version and flagged for review. This conservative design can require manual conflict resolution; it does not overwrite an original.

Exit `0`: command completed, including review/manual/pending/missing outcomes. Exit `1`: operational failure, missing auth, interruption or partial operational failure. Exit `2`: invalid input/configuration. Routine errors contain safe codes, not raw provider responses. Handle failures per account; successful documents remain saved.
