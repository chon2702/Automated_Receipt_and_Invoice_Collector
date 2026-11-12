# Google Ads invoice collection

This collector downloads original Google Ads invoice PDFs through the Google Ads REST API (v26). It only works for accounts on **monthly invoicing**. Accounts that pay by automatic card payments do not have API invoices; keep their email or manual workflow. [Google Ads billing requirements](https://developers.google.com/google-ads/api/docs/billing/overview)

Live sign-in and collection have not been tested yet. The code and request handling are verified offline with simulated responses only. See [Live pilot](#live-pilot) for what still needs confirming.

## Issue month and service period

`--month` selects invoices by the month they were **issued**, not the month of service. `collect --month 2026-11` asks Google for invoices issued in November 2026, which normally cover October usage.

Each saved document keeps both dates separately:

- `issue_date` is the invoice date, for example `2026-11-03`. The original is stored under the issue month folder, `documents/<vendor>/<account>/2026-11/`.
- `period_start` and `period_end` are the service dates, for example `2026-10-01` to `2026-10-31`. Billing expectations match on these dates.

To collect the invoice for October usage, run the November issue month. [Invoice listing and download](https://developers.google.com/google-ads/api/docs/billing/invoice)

## Identifiers

Find these in Google Ads under **Billing** and in the account switcher. Enter every ID as a string of digits without hyphens.

| Setting | Required | Format | Meaning |
|---|---|---|---|
| `google_ads_customer_id` | yes | exactly 10 digits | The serving Google Ads account whose invoices are listed |
| `google_ads_billing_setup_id` | yes | digits | The billing setup ID under that customer |
| `google_ads_login_customer_id` | no | exactly 10 digits | The paying manager account, when access goes through a manager |

These settings are rejected on accounts that do not use `google_ads` collection.

For **consolidated billing**, several serving accounts can return the same invoice. Configure one representative serving account for the payments account. Duplicate detection works within one configured account, so listing the same invoice through several accounts would save it more than once.

## Credentials for the pilot

For the first pilot, the collector reads two values from the process environment. It has no login command and does not refresh tokens.

- `GOCO_GOOGLE_ADS_ACCESS_TOKEN`: a short-lived OAuth access token with the `https://www.googleapis.com/auth/adwords` scope. It must be issued to a Google user who can view invoices for the account. Get it through your organization's approved OAuth workflow. [Authorization headers](https://developers.google.com/google-ads/api/rest/auth)
- `GOCO_GOOGLE_ADS_DEVELOPER_TOKEN`: the Google Ads API developer token from the manager account's API Center. A token approved only for test accounts cannot read production billing data.

Set both values in the current PowerShell session only. `Read-Host -MaskInput` (PowerShell 7.1 and later) keeps them out of the console and command history.

```powershell
$env:GOCO_GOOGLE_ADS_ACCESS_TOKEN = Read-Host -MaskInput 'Google Ads access token'
$env:GOCO_GOOGLE_ADS_DEVELOPER_TOKEN = Read-Host -MaskInput 'Google Ads developer token'
```

When you are finished, remove them:

```powershell
Remove-Item Env:GOCO_GOOGLE_ADS_ACCESS_TOKEN, Env:GOCO_GOOGLE_ADS_DEVELOPER_TOKEN
```

Do not put tokens in the configuration file. A missing, malformed, expired, or rejected token is reported as `auth_required`, and the command exits with code 1.

## Account entry

Add this synthetic entry to the `accounts` array in your private configuration. Replace the IDs with your own and use an existing owner ID. Leave out `google_ads_login_customer_id` if no manager account is involved.

```json
{
  "id": "google-ads",
  "vendor_id": "google-ads",
  "product": "Google Ads",
  "owner_id": "person1",
  "collection": "google_ads",
  "google_ads_customer_id": "1234567890",
  "google_ads_billing_setup_id": "5550001",
  "google_ads_login_customer_id": "9876543210"
}
```

If Google Ads invoices already arrive through Gmail, you can switch the existing account entry to `google_ads` and keep its ID. That keeps duplicate detection for identical PDFs of the same document type. Older Gmail scans no longer count toward this account's completeness.

## Running

```powershell
.\.venv\Scripts\goco.exe --config private/config.json config validate
.\.venv\Scripts\goco.exe --config private/config.json collect --account google-ads --month 2026-11 --dry-run --format json
.\.venv\Scripts\goco.exe --config private/config.json collect --account google-ads --month 2026-11
```

A **dry run** lists the invoices and reports one `review_required` / `google_ads_dry_run_invoice_available` outcome per unique invoice ID. It downloads no PDFs and writes no documents or run records.

A **collection run** downloads each original PDF and stores the exact bytes through the same storage, duplicate detection, and reporting as Gmail and manual imports. Rerunning the same month reports `duplicate` with the same document ID.

Invoices are saved as `invoice`, and credit memos as `credit_note` with a negative amount. Amounts are converted from micros exactly. Other or new invoice types are saved as `unknown` for review. An invoice missing its issue date, service dates, or amount is still saved, but left unverified for review.

An empty listing leaves completeness unknown. It does not prove that no invoice exists.

## PDF download safety

The PDF is fetched with a plain `GET` that carries only the bearer token. The developer token and manager header are not sent. Before the token is attached, the collector checks the `pdfUrl` and rejects:

- any scheme other than `https`
- a port other than 443
- user names or passwords in the URL
- fragments, whitespace, or backslashes
- any host except an exact approved host

Redirects are never followed. Downloads use connect and read timeouts, the configured `max_bytes` limit, and a `%PDF-` signature check.

The approved hosts are listed in `APPROVED_PDF_HOSTS` in `src/goco/models/integrations/vendors/google_ads.py`. The list currently contains only `payments.google.com`. **This host is provisional:** it has not yet been confirmed against a real invoice URL.

Tokens and full download URLs are never included in outcomes, run records, sidecars, or error messages. Saved records identify the source only as `<customer ID>:<billing setup ID>:<invoice ID>`.

## Outcomes

| Outcome | Reason | Meaning |
|---|---|---|
| `auth_required` | `google_ads_credentials_missing` | An environment variable is missing or malformed |
| `auth_required` | `google_ads_access_denied` | Google rejected the listing request (401/403) |
| `auth_required` | `google_ads_pdf_access_denied` | Google rejected one PDF download (401/403) |
| `manual_required` | `google_ads_not_invoiced_customer` | The account is not on monthly invoicing; use email or manual import |
| `failed` | `google_ads_request_failed` | The listing failed or returned an unexpected response |
| `failed` | `google_ads_pdf_host_rejected` | The PDF URL is not on an approved host; see below |
| `failed` | `google_ads_pdf_redirect_rejected` | The PDF request returned a redirect |
| `failed` | `google_ads_pdf_download_failed` / `_too_large` / `google_ads_download_not_pdf` | One download failed |
| `review_required` | `google_ads_pdf_unavailable` | The invoice has no PDF URL |
| `review_required` | `google_ads_invoice_metadata_incomplete` | The invoice ID is missing or unusable |

Per-invoice failures do not stop the run. Earlier and later invoices are still collected.

## Live pilot

These checks need billing access to the company account. Until they are done, Google Ads collection is offline-tested only.

1. Confirm the account uses monthly invoicing. Confirm the serving customer ID, the paying manager ID if one is used, and the billing setup ID. If the account uses automatic payments, keep the email or manual route.
2. Confirm the developer token can access production accounts, and that the access token's Google user can view invoices.
3. Run a dry run for a closed issue month and compare the invoice count with the Google Ads billing portal.
4. Run collection. If every download fails with `google_ads_pdf_host_rejected`, check the real `pdfUrl` host in a controlled debug session. Add it to `APPROVED_PDF_HOSTS` only after confirming it is a Google-owned invoice host.
5. Compare the saved PDF bytes with the file downloaded from the portal. Check the issue date, service dates, and amount.
6. Rerun the same month and confirm it reports `duplicate` with the same document ID.
