# Gmail test for the VA and VL managers

`config/manager.example.json` is a compact template for one manager's Gmail mailbox. It has one owner, one mailbox and the 21 vendors with PDF attachments. `account_defaults` gives every vendor the same owner, `gmail` collection and mailbox, so each vendor line only needs `id`, `vendor_id`, `product` and, where useful, `search_aliases`.

## Prepare one copy per manager

Copy the template twice into the private folder:

```powershell
Copy-Item config\manager.example.json private\va.json
Copy-Item config\manager.example.json private\vl.json
```

In each copy, replace the placeholders. The examples are for VA; use `vl` in the VL copy.

| Field | Placeholder | Fill in (VA) |
|---|---|---|
| `storage_root` / `state_dir` | `manager/documents`, `manager/state` | `va/documents`, `va/state` |
| `owners[0].id` | `manager` | `va` |
| `owners[0].display_name` | `MANAGER_NAME` | the manager's name |
| `mailboxes[0].id` | `manager-gmail` | `va-gmail` |
| `mailboxes[0].address` | `manager@example.test` | the manager's Gmail address |
| `mailboxes[0].secret_ref` | `goco-manager-gmail` | `goco-va-gmail` |
| `account_defaults.owner_id` | `manager` | `va` (same as `owners[0].id`) |
| `account_defaults.mailbox_id` | `manager-gmail` | `va-gmail` (same as `mailboxes[0].id`) |

Then tailor `accounts` to the services this mailbox actually receives. Delete vendors that do not bill to this address, and add `search_aliases` where a vendor appears under another name. `oauth_client_file` stays `oauth-client.json`: the copies use the existing `private\oauth-client.json`.

An account can still override a default, for example `"collection": "manual"` for a vendor without e-mail invoices. A manual or API account never receives the default mailbox. Check each copy:

```powershell
.\.venv\Scripts\goco.exe --config private\va.json config validate
```

Separate `storage_root`, `state_dir` and `secret_ref` values keep both managers' documents and Gmail logins apart, even on the same computer. The manager's Gmail address must be allowed to use the OAuth client. If its consent screen is in testing mode, add the address as a test user first.

## The manager's two commands

From the project folder on the manager's Windows computer (VA shown):

```powershell
.\.venv\Scripts\goco.exe --config private\va.json auth login --mailbox va-gmail
.\.venv\Scripts\goco.exe --config private\va.json collect --all --month 2026-10 --dry-run
```

1. **Login** opens the browser for read-only Gmail consent. It checks that the signed-in account is the configured address, and stores the token in Windows Credential Manager.
2. **Dry run** shows per vendor which PDFs would be stored, which would need review, and which accounts had no matches. It downloads no attachments and writes nothing. A dry run does not refresh the login, so run it soon after logging in; if it reports `LOGIN REQUIRED`, log in again.

The real collection is the same command without `--dry-run`.
