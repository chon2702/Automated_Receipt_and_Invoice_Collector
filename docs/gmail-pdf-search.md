# Gmail PDF search by vendor name

Gmail accounts find invoice PDFs by the account's `product` name and optional `search_aliases`. Per vendor, the account, the mailbox and the search names are enough. Invoice numbers, exact file names and delivery days are not configuration fields.

```json
{
  "id": "anthropic-claude",
  "vendor_id": "anthropic",
  "product": "Claude",
  "owner_id": "person1",
  "collection": "gmail",
  "mailbox_id": "billing-mail",
  "search_aliases": ["Anthropic"]
}
```

A search name needs at least two letters or digits. It must not contain double quotes or backslashes, and is at most 64 characters long. Names match whole words, ignoring case and punctuation between words: `Make.com` matches `billing@make.com`, but `Claude` does not match `Claudette`.

Avoid common words as aliases. `Make` would match "make sure", for example; use `Make.com` instead. A product name that is a common word, such as `Motion`, is still searched. Watch its first runs for review cases.

## How a run decides

1. **Gmail preselection.** One bundled search per mailbox covers the requested month: messages with a PDF attachment and any selected account's name. Long name lists are split into several queries. All result pages are read, and each message is handled once, even if several queries return it.
2. **Local re-check per PDF.** The vendor is taken from the attachment's file name first, then the sender and subject, and only then the message text. A lower source is used only when the higher ones name no vendor. A receipt sent through Stripe is therefore assigned when the vendor appears in the subject, the file name or the message text. All name-search accounts of the mailbox take part in this check, so an ambiguous PDF is detected even when only one account is selected.
3. **Invoice feature per PDF.** The file name decides the document type:
   - `invoice`: Invoice, Rechnung
   - `receipt`: Receipt, Zahlungsbeleg, Zahlungsbestätigung, Quittung
   - `credit_note`: Credit note, Credit memo, Gutschrift

   A vendor name alone is not enough to store a PDF.

| Situation | Result |
|---|---|
| One vendor and one document type | Stored with that type. An invoice and a receipt in one message become two documents. |
| Several vendors | `review_required` / `ambiguous_vendor` for each vendor concerned |
| Type words only in subject or text, or several types in the file name | `review_required` / `document_type_unclear` |
| No invoice feature anywhere, such as a support e-mail with a PDF | `skipped` / `no_invoice_feature`; not stored |

Stored PDFs use the existing Gmail attachment download, file checks, storage and duplicate detection. A new PDF is reported as `review_required` / `metadata_unverified` until its dates and amounts are verified. The same bytes for the same account and type are reported as `duplicate`. Review cases get a `review_id`; the review record names the message and attachment.

## Output

Text output shows one line per selected account, followed by the individual outcomes:

```text
Collection: {"duplicate": 2}
anthropic-claude | ok | saved=0 (metadata review 0) duplicate=2 review=0 skipped=0
calendly | ok, no matches | saved=0 (metadata review 0) duplicate=0 review=0 skipped=0
aws | FAILED (gmail_access_denied) | saved=0 (metadata review 0) duplicate=0 review=0 skipped=0
```

- `ok, no matches` means the search succeeded and found nothing for that account.
- `FAILED` or `LOGIN REQUIRED` means the retrieval itself failed, and the command exits with code 1.
- `--format json` includes the same data under `accounts`.

## Dry run

A dry run reads message structure and text to make the same per-PDF decisions. Instead of storing, it reports `dry_run_potential_invoice` (or another type). It never downloads or decodes attachment contents, writes nothing and does not refresh the Gmail token.

## Portal link notifications

Link notifications, such as Google Ads "billing documents are available", carry no PDF. They keep an explicit rule with `"purpose": "link_notice"` and the existing sender, recipient and subject fields. A matching message becomes `manual_required` / `no_supported_attachment` with a review record, as before. Accounts with such a rule do not take part in the name search, and `search_aliases` are rejected on them.

## Migrating old attachment rules

Earlier configurations collected PDFs through sender rules. Configuration loading now stops with an error for every rule without `purpose`, so the behavior never changes silently:

```text
rules[0]: rules no longer collect PDF attachments; delete this rule and let the account search by product and search_aliases, or set "purpose": "link_notice" if it only flags portal link e-mails
```

For each such rule:

1. **PDF attachment rule:** delete it. Check the account's `product` and add `search_aliases` if the vendor appears under another name, for example in the sender name or domain.
2. **Link notification rule:** add `"purpose": "link_notice"`.
3. Run `config validate`, then a dry run for a known month. Compare the reported documents with the earlier results before the first real run.

Documents stored earlier stay as they are. A rerun reports them as duplicates when the new search assigns them to the same account and document type.
