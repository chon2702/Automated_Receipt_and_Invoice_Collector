"""Download original AWS billing documents using the AWS Invoicing API."""

from calendar import monthrange
from datetime import date, datetime
import re
from urllib.parse import urlsplit

import requests

from goco.models import CollectionOutcome, DocumentCandidate, InvoiceMetadata, SourceRef


from .errors import CollectionError


class DownloadForbidden(CollectionError):
    def __init__(self):
        super().__init__("aws_pdf_access_denied")


def _call(operation, **parameters):
    try:
        return operation(**parameters)
    except Exception as error:
        from botocore.exceptions import BotoCoreError, ClientError

        if isinstance(error, ClientError):
            code = error.response.get("Error", {}).get("Code")
            if code in {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"}:
                raise CollectionError("aws_login_required", "auth_required") from None
            if code in {"AccessDenied", "AccessDeniedException"}:
                raise CollectionError("aws_invoicing_permission_denied", "auth_required") from None
            raise CollectionError("aws_invoicing_request_failed") from None
        if isinstance(error, BotoCoreError):
            if type(error).__name__ in {
                "NoCredentialsError", "PartialCredentialsError", "ProfileNotFound",
                "SSOTokenLoadError", "UnauthorizedSSOTokenError", "TokenRetrievalError",
            }:
                raise CollectionError("aws_login_required", "auth_required") from None
            raise CollectionError("aws_sdk_request_failed") from None
        raise


def make_client(profile):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise CollectionError("aws_sdk_missing_install_aws_extra") from None

    def create_client():
        return boto3.Session(profile_name=profile).client(
            "invoicing",
            region_name="us-east-1",
            endpoint_url="https://invoicing.us-east-1.api.aws",
            config=Config(connect_timeout=10, read_timeout=30,
                          retries={"mode": "standard", "total_max_attempts": 3}),
        )

    return _call(create_client)


def download_pdf(url, limit):
    if not isinstance(url, str):
        raise CollectionError("aws_invalid_pdf_url")
    try:
        parsed = urlsplit(url)
        allowed = (
            parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and re.fullmatch(r"(?:[a-z0-9.-]+\.)?s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com", parsed.hostname or "")
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise CollectionError("aws_invalid_pdf_url")

    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code == 403:
                    raise DownloadForbidden()
                if response.status_code != 200:
                    raise CollectionError("aws_pdf_download_failed")
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise CollectionError("aws_pdf_too_large")
    except requests.RequestException:
        # Request exceptions can contain the signed URL.
        raise CollectionError("aws_pdf_download_failed") from None
    if not data.startswith(b"%PDF-"):
        raise CollectionError("aws_invalid_pdf_content")
    return bytes(data)


def _invoice_pdf(client, invoice_id):
    response = _call(client.get_invoice_pdf, InvoiceId=invoice_id)["InvoicePDF"]
    if response["InvoiceId"] != invoice_id:
        raise CollectionError("aws_invoice_identity_mismatch")
    return response


def _pdf_url(response, document_id):
    if document_id is None:
        return response["DocumentUrl"]
    for document in response.get("SupplementalDocuments", []):
        if document["DocumentId"] == document_id:
            return document["DocumentUrl"]
    raise CollectionError("aws_supplement_unavailable")


def _invoice_documents(client, account, summary, year, month, limit):
    invoice_id = summary["InvoiceId"]
    pdf = _invoice_pdf(client, invoice_id)
    amount = summary.get("PaymentCurrencyAmount") or summary.get("BaseCurrencyAmount") or {}
    total, currency = amount.get("TotalAmount"), amount.get("CurrencyCode")
    issue_date = summary.get("IssuedDate")
    kind = {"INVOICE": "invoice", "CREDIT_MEMO": "credit_note"}.get(summary.get("InvoiceType"), "unknown")
    metadata = InvoiceMetadata(
        issue_date=issue_date.date().isoformat() if isinstance(issue_date, datetime) else None,
        period_start=date(year, month, 1).isoformat(),
        period_end=date(year, month, monthrange(year, month)[1]).isoformat(),
        invoice_id=invoice_id, amount=total if currency else None, currency=currency if total else None,
        provenance="aws", verified=kind != "unknown",
    )
    supplement_ids = [item["DocumentId"] for item in pdf.get("SupplementalDocuments", [])]
    for document_id in [None, *dict.fromkeys(supplement_ids)]:
        try:
            try:
                content = download_pdf(_pdf_url(pdf, document_id), limit)
            except DownloadForbidden:
                pdf = _invoice_pdf(client, invoice_id)
                content = download_pdf(_pdf_url(pdf, document_id), limit)
            supplemental = document_id is not None
            origin = f"{account['aws_account_id']}:{invoice_id}"
            if supplemental:
                origin += f":supplement:{document_id}"
            yield DocumentCandidate(
                account_id=account["id"], vendor_id=account["vendor_id"],
                document_type="unknown" if supplemental else kind,
                original_filename="aws-supplement.pdf" if supplemental else "aws-invoice.pdf",
                content=content, source=SourceRef("aws", origin),
                metadata=InvoiceMetadata(provenance="aws") if supplemental else metadata,
                owner_id=account["owner_id"],
            )
        except CollectionError as error:
            yield CollectionOutcome(account["id"], error.outcome, error.reason)


def collect(account, billing_month, dry_run, limit):
    client = make_client(account["aws_profile"])
    year, month = map(int, billing_month.split("-"))
    billing_period = {"Year": year, "Month": month}
    parameters = {
        "Selector": {"ResourceType": "ACCOUNT_ID", "Value": account["aws_account_id"]},
        "Filter": {"BillingPeriod": billing_period},
        "MaxResults": 100,
    }
    seen_invoices, seen_tokens = set(), set()
    while True:
        response = _call(client.list_invoice_summaries, **parameters)
        for invoice in response.get("InvoiceSummaries", []):
            if (invoice.get("AccountId") != account["aws_account_id"]
                    or invoice.get("BillingPeriod") != billing_period or not invoice.get("InvoiceId")):
                yield CollectionOutcome(account["id"], "review_required", "aws_summary_scope_mismatch")
                continue
            invoice_id = invoice["InvoiceId"]
            if invoice_id in seen_invoices:
                continue
            seen_invoices.add(invoice_id)
            if dry_run:
                yield CollectionOutcome(account["id"], "review_required", "aws_dry_run_invoice_available")
            else:
                try:
                    yield from _invoice_documents(client, account, invoice, year, month, limit)
                except CollectionError as error:
                    yield CollectionOutcome(account["id"], error.outcome, error.reason)
        token = response.get("NextToken")
        if not token:
            return
        if token in seen_tokens:
            raise CollectionError("aws_repeated_page_token")
        seen_tokens.add(token)
        parameters["NextToken"] = token
