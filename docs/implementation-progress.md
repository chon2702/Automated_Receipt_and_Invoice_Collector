# Implementation ledger â€” plan: docs/superpowers/plans/2026-11-05-goco-mvp.md

User authorized implementation on 5 November 2026. Execute natively; Gmail and manual first, AWS next.

Ruling: No Git repository exists. Work in the supplied workspace and retain this ledger instead of Git-specific helper scripts/commits. No branch or worktree exists to isolate; cost is no Git checkpoint until a repository is initialized.

Pre-flight: Tasks 1â€“6 share models/config; Tasks 2â€“6 share storage/outcomes; Tasks 3/6 share coverage/run reports; Tasks 4â€“6 share Gmail message types. Use the specification as authority for received-month selection and explicit verified service-period coverage.

Ruling: Use official google-auth/installed OAuth packages with requests for documented Gmail REST reads, rather than google-api-python-client transport. One HTTP transport enforces separate connect/read timeouts and disables implicit refresh in dry runs. Cost: maintain a small REST adapter instead of discovery-generated calls.

Live pilot needs company OAuth registration, account mapping, private samples and shared storage access; offline implementation proceeds without these.

Task 1: complete â€” configuration/CLI suite 10 passed, following 10 observed missing-package failures.
Implementation continued in the local autoinvoices project folder.

Tasks 2â€“3: core storage/obligation behavior green (22 tests). Windows SQLite connections explicitly closed after commit; recovery tests caught rename-blocking handles. CLI wiring and broader edge tests continue with Task 6.

Tasks 4â€“6: implemented Gmail, rules, CLI and reports. Suite: 47 passed after failing tests for each added boundary. Live pilot explicitly pending company inputs.
Ruling: Keep unresolved metadata conservative: any unreviewed same-account candidate can block missing status. Cost: extra manual review instead of a false missing claim.
Git integration remains unavailable; files and ledger retained in place.

Final independent review: four consequential findings, all reproduced before correction and verified with five regression cases. Coverage now stops at elapsed time; SQLite faults produce failed portable run coverage; disabled rules invalidate earlier coverage; review items have safe IDs backed by private source records.

Final suite: 52 passed. Installed CLI smoke: configuration, import, duplicate, status, byte preservation, rebuild and report passed using synthetic files in a path with spaces. Dependency check: no broken requirements. No real credentials or company invoices used.

Declined-to-judge resolutions: live OAuth/refresh and vault operation, vendor matching and mailbox completeness, shared access/sync and backup remain explicit pilot requirements. The operations guide is now written and links these requirements. Cost of proceeding without those checks: collector readiness for real company records remains unproven; do not claim live release readiness.

No deferred minor code-review findings. Company pilot A8 remains pending; all local implementation tasks have working code and regression coverage. Task file names were consolidated into behavior-based suites; no Git commit steps were executed because no repository exists.
