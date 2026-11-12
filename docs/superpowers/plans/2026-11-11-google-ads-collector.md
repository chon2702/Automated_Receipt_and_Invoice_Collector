# Google Ads Invoice Collector Implementation Plan

> **For Claude:** Use the implementation-plan workflow available in your session. Work through the checkboxes in order in the existing `C:/Users/my_goconut/autoinvoices` checkout.

**Goal**
Add offline-tested collection of original Google Ads invoice PDFs for accounts on monthly invoicing.

**Architecture**
Add one provider adapter under the MVC model integration package. Register it with the existing collection runner and reuse its storage, duplicate detection, status, and CLI command. The adapter lists invoices by issue month and records the service dates separately.

**Tech stack**
Python 3.12+, existing `requests` dependency, pytest, and Google Ads REST API v26. Confirm the API version is still supported when implementation starts.

**Spec**
The Google Ads contract below extends the shared [collector specification](../specs/2026-11-05-goco-mvp-design.md).

## Scope and eligibility

Google Ads is the proposed next provider because Google documents both invoice listing and original PDF retrieval. The API requires monthly invoicing. Accounts that use automatic card payments should keep their email or manual workflow. Company eligibility and the live PDF host remain to be verified once billing access is available. [Google Ads billing requirements](https://developers.google.com/google-ads/api/docs/billing/overview)

The implementation can be completed with synthetic responses. Use the existing checkout as the baseline. It includes Azure edits that may be uncommitted, so preserve them. Check the working tree before editing and keep this change focused on the Google Ads collector. Leave live sign-in and collection for the later pilot.

## Google Ads contract

- Account keys are `collection: google_ads`, `google_ads_customer_id`, `google_ads_billing_setup_id`, and optional `google_ads_login_customer_id`. Validate customer and manager IDs as strings of exactly 10 digits and the setup ID as a nonempty digit string. Reject these settings for other collection methods. Use a serving customer and the paying manager ID when the billing arrangement requires it.
- For the first pilot, use `GOCO_GOOGLE_ADS_ACCESS_TOKEN` from the process environment. Google sunset developer tokens on September 9, 2026. API access is managed by the Cloud project behind the OAuth credentials. Use a project whose access level and permissible use include billing on production accounts. [Developer token sunset](https://developers.google.com/google-ads/api/docs/api-policy/developer-token) The access token must be short-lived, issued for the same Google user who can view the invoice, and carry the `https://www.googleapis.com/auth/adwords` scope. An operator obtains it through their approved OAuth workflow. Keep OAuth provisioning, token refresh, and additional CLI login commands outside this implementation. Report missing or expired credentials as `auth_required`. [Google Ads authorization headers](https://developers.google.com/google-ads/api/rest/auth)
- List with `GET https://googleads.googleapis.com/v26/customers/{customer_id}/invoices`. Use the documented `billingSetup`, `issueYear`, and `issueMonth` query parameters. Construct the setup resource as `customers/{customer_id}/billingSetups/{setup_id}`. `--month 2026-11` sends issue year `2026` and issue month `NOVEMBER`. The v26 response contains an `invoices` collection. [Invoice listing and download guide](https://developers.google.com/google-ads/api/docs/billing/invoice) · [v26 response](https://developers.google.com/google-ads/api/reference/rpc/v26/ListInvoicesResponse)
- For each invoice, use `id`, `issueDate`, `serviceDateRange`, `type`, `currencyCode`, `totalAmountMicros`, and `pdfUrl`. Preserve `issueDate` separately from the service period. Convert integer micros to decimal currency using `Decimal`, including zero and negative values. Follow the documented invoice enum. Preserve unknown document types for review and leave missing metadata unverified. [Invoice fields](https://developers.google.com/google-ads/api/reference/rpc/v26/Invoice) · [Invoice types](https://developers.google.com/google-ads/api/reference/rpc/v26/InvoiceTypeEnum.InvoiceType)
- Download the original with a bearer-only GET. Validate the scheme, port, and exact approved Google invoice hostname before attaching the bearer token. Use `payments.google.com` as the initial explicit allowlist and synthetic fixture host. This is a provisional choice pending a real invoice URL. If the pilot returns another host, verify it before adding it. Reject credentials in URLs, redirects, and lookalike hosts. Apply timeouts, the existing byte limit, and the existing PDF signature check. Keep tokens and the full PDF URL out of exceptions, reports, and sidecars. [Authenticated PDF download](https://developers.google.com/google-ads/api/docs/billing/invoice#download_an_invoice_pdf)
- Dry run performs invoice listing and emits one `review_required` outcome per unique invoice ID. It makes zero PDF requests and creates zero storage or run files. A normal run yields `DocumentCandidate` objects with a stable source ID scoped to the configured billing setup and invoice ID. Configure one representative serving account for a consolidated payments account, since multiple setups can return the same invoice and the existing store deduplicates within an account. [Consolidated billing behavior](https://developers.google.com/google-ads/api/docs/billing/invoice)
- `NOT_INVOICED_CUSTOMER` is a manual fallback outcome. Permission failures are `auth_required`. A missing or rejected PDF yields a safe per-invoice outcome and later invoices still run. Empty lists leave completeness unknown. Prior Gmail scan coverage cannot certify a Google Ads account.

## Task 1. Provider adapter

**Files**
Create `src/goco/models/integrations/vendors/google_ads.py` and `tests/test_google_ads_invoices.py`.

**Interface**
`collect(account, billing_month, dry_run, limit)` yields `DocumentCandidate` or `CollectionOutcome` using the existing vendor contract. The shared caller already handles `CollectionError` from `models/integrations/vendors/errors.py`.

- [x] Add a synthetic REST test for November issue month with October service dates and a PDF. Assert exact list parameters and headers, the bearer-only download, the original bytes, verified dates, and exact decimal conversion. Include a duplicate invoice ID and assert one download.
- [x] Run the focused test and confirm it fails because the adapter is missing.
- [x] Implement list, metadata mapping, and bounded PDF download. Isolate per-invoice validation and download failures while preserving successful earlier and later records.
- [x] Cover missing credentials, 401/403, the structured ineligible-account error, empty results, unknown type, invalid PDF, an oversized stream, redirect rejection, and an unapproved PDF host. Assert sensitive strings are absent from outcomes. Test that zero and negative amounts survive conversion.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/test_google_ads_invoices.py -q`.

## Task 2. Configuration, command routing, and handoff

**Files**
Modify `src/goco/models/accounts/config.py`, `src/goco/controller/runner.py`, `src/goco/controller/commands.py`, and the CLI help in `src/goco/view/cli.py`. Add `docs/google-ads-collection.md` and link it from `README.md`.

- [x] Add a runner-level test using the real config loader and document store. A dry run leaves storage empty. A normal run stores the exact original. A rerun reports duplicate with the same document ID. Include a config validation test for invalid IDs and provider fields on another collector.
- [x] Register `google_ads` in the existing API collector mapping and exclude it from Gmail coverage when building reports. Keep the existing `collect` command, output formats, and exit codes.
- [x] Add setup instructions and a synthetic account entry. Document the pilot token inputs, monthly invoicing eligibility, issue-month semantics, and the exact-host assumption. Use `collect --account google-ads --month 2026-11 --dry-run --format json` as the example.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check`. Report test counts and the files changed. Leave live verification pending and keep existing Azure work separate.

## Live pilot after access is available

Confirm monthly invoicing, the Cloud project’s billing API access, and the serving customer, paying manager, and billing setup. Google Ads test accounts have no billing, so use synthetic responses until this production pilot is available. [Test account limits](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts) Compare a closed issue month with the Google Ads billing portal. Verify the exact PDF origin if the initial host check rejects it, compare the saved bytes with the portal file, and rerun to confirm duplicate handling. If the company account uses automatic payments, keep the email or manual route for that account.
