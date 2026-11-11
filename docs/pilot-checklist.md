# Company pilot — pending

Implementation verification uses synthetic data and simulated Gmail transport. No live company login, real-message rule validation, accounting access or backup restore has been demonstrated yet.

## Inputs

- [ ] Company-approved Google Cloud project/Desktop OAuth client and authorized Gmail user.
- [ ] Confirmed billing account/product/owner mapping; folder labels VA/VL alone are insufficient.
- [ ] Private sample original messages for two or three vendors with reported attached PDFs.
- [ ] Exact sender, recipient, subject and attachment rules verified against samples; shared-sender ambiguity evaluated.
- [ ] Approved shared destination, single writer workstation, authorized accounting reader and backup owner.
- [ ] Explicit due dates/service periods and optional verified delivery windows; annual subscriptions remain annual.

## Evidence before live release

- [ ] Normal Gmail consent succeeds for the configured identity; a different user is rejected.
- [ ] Dry-run discovery produces no document/state/keyring changes and no attachment-content fetch.
- [ ] Collect originals from the chosen month; privately compare attachment bytes and SHA-256.
- [ ] Confirm linked-only/body-only messages produce actionable manual/review outcomes.
- [ ] Re-run collection; existing files/UUIDs remain stable and duplicate counts are correct.
- [ ] Review document type/service period using `document verify`; original bytes stay unchanged and history is retained.
- [ ] Import one manually obtained invoice through the same storage/deduplication path.
- [ ] Demonstrate unknown/pending/satisfied obligations and a deliberately incomplete scan without false missing claims.
- [ ] Demonstrate token refresh/revocation and network failure without raw secrets in logs.
- [ ] Accounting user can open original files through the approved shared location; confirm remote synchronization if applicable.
- [ ] Restore a copied synthetic backup to a separate location, rebuild the index and compare hashes/provenance/run outcomes.
- [ ] Record operator/date/results and approve supported vendor/account rules individually. Do not claim all 14 reported email-PDF vendors work from the first few samples.

## Next release

- [ ] AWS API feasibility refreshed against actual account/role; separate implementation plan and real invoice test.
