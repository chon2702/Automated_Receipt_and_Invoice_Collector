# Azure invoice collection

This collector supports Azure public-cloud billing profiles under Microsoft Customer Agreements and Microsoft Partner Agreements. It uses the invoice billing period to select documents, so an invoice issued in November can belong to October. For a period that spans months, run the collector for the month containing the period start. [List invoices by billing profile](https://learn.microsoft.com/en-us/rest/api/billing/invoices/list-by-billing-profile?view=rest-billing-2024-04-01)

Install Azure CLI and the optional Python dependency. When the company account is available, sign in with the identity that can read the relevant billing profile and download its invoices. [Azure CLI credentials](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.azureclicredential?view=azure-python)

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[azure]'
az login --tenant YOUR_TENANT_ID
```

Add this entry to the `accounts` array in your private configuration. Replace the billing account and profile identifiers with the values from Cost Management + Billing, and use an existing owner ID.

```json
{
  "id": "azure-billing",
  "vendor_id": "azure",
  "product": "Azure",
  "owner_id": "person1",
  "collection": "azure",
  "azure_billing_account": "12345",
  "azure_billing_profile": "example-profile"
}
```

```powershell
.\.venv\Scripts\goco.exe --config private/config.json collect --account azure-billing --month 2026-10 --dry-run --format json
.\.venv\Scripts\goco.exe --config private/config.json collect --account azure-billing --month 2026-10
```

The dry run lists available documents. A collection run saves original PDF invoices, credit notes, and tax receipts through the shared duplicate checks. ZIP responses are reported for manual handling. An empty listing leaves completeness unknown. [Download invoice documents](https://learn.microsoft.com/en-us/rest/api/billing/invoices/download-by-billing-account?view=rest-billing-2024-04-01)

For the live pilot, choose a closed period with a known invoice, compare the original PDFs with the billing portal, and rerun collection to verify duplicate handling.
