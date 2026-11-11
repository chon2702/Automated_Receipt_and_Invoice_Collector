# AWS invoice collection

Install the optional AWS SDK in the project environment.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[aws]'
```

Configure a named AWS profile with `invoicing:ListInvoiceSummaries` and `invoicing:GetInvoicePDF` access to the invoiced account. Use your organization's normal sign-in for the profile. For an IAM Identity Center profile, sign in with `aws sso login --profile billing`. The collector uses the AWS Invoicing endpoint in `us-east-1`. [AWS invoice retrieval guide](https://aws.amazon.com/blogs/aws-cloud-financial-management/automate-aws-invoice-retrieval-with-new-programmatic-apis/)

Add an account entry to your private collector configuration and replace the sample values with your existing owner ID, invoiced AWS account ID, and profile name.

```json
{
  "id": "aws-billing",
  "vendor_id": "aws",
  "product": "AWS",
  "owner_id": "person1",
  "collection": "aws",
  "aws_profile": "billing",
  "aws_account_id": "123456789012"
}
```

Use the payer account for consolidated invoices. If AWS documents already come through Gmail, switch the existing account entry to AWS collection and keep its ID. This preserves duplicate detection within that account for identical PDFs with the same document type. AWS uses the billed service month even when the invoice issue date falls in the next month.

```powershell
.\.venv\Scripts\goco.exe --config private/config.json collect --account aws-billing --month 2026-10 --dry-run --format json
.\.venv\Scripts\goco.exe --config private/config.json collect --account aws-billing --month 2026-10
```

Dry run lists available invoices through the authenticated AWS API. The collection run downloads originals and uses the same storage as Gmail and manual import. Supplemental PDFs are saved for review because their accounting document type can vary. An empty AWS listing leaves completeness unknown. Expiring download links stay in memory during collection.

The SDK request and storage flow have been verified locally. The first live pilot requires the company's billing profile and a known invoice month for comparison with the AWS console.
