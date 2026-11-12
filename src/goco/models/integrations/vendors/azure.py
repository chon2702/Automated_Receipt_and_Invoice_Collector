"""Collect invoice PDFs from an Azure billing profile."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import re
from time import sleep
from urllib.parse import quote, urlsplit

import requests

from goco.models import CollectionOutcome, DocumentCandidate, InvoiceMetadata, SourceRef
from .errors import CollectionError

ARM_HOST = "management.azure.com"
API_VERSION = "2024-04-01"
DOCUMENT_KINDS = {"Invoice": "invoice", "CreditNote": "credit_note", "TaxReceipt": "receipt"}


def get_access_token():
    try:
        from azure.core.exceptions import AzureError
        from azure.identity import AzureCliCredential
    except ImportError:
        raise CollectionError("azure_sdk_missing_install_azure_extra") from None
    try:
        credential = AzureCliCredential(process_timeout=15)
        return credential.get_token(f"https://{ARM_HOST}/.default").token
    except AzureError:
        raise CollectionError("azure_login_required", "auth_required") from None


def _https_host(url):
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.fragment):
            return parsed.hostname
    except ValueError:
        pass
    return None


def _arm(session, token, method, url, params=None):
    if _https_host(url) != ARM_HOST:
        raise CollectionError("azure_invalid_billing_url")
    try:
        with session.request(method, url, params=params, headers={"Authorization": f"Bearer {token}"},
                             timeout=(10, 30), allow_redirects=False) as response:
            if response.status_code in (401, 403):
                raise CollectionError("azure_billing_access_required", "auth_required")
            if response.status_code == 202:
                return None, response.headers
            if response.status_code != 200:
                raise CollectionError("azure_billing_request_failed")
            return response.json(parse_float=Decimal), response.headers
    except (requests.RequestException, ValueError):
        raise CollectionError("azure_billing_request_failed") from None


def _download_url(session, token, invoice_url, document_name):
    params = {"api-version": API_VERSION}
    if document_name is not None:
        params["documentName"] = document_name
    payload, headers = _arm(session, token, "POST", f"{invoice_url}/download", params)
    location = headers.get("Location")
    for _ in range(12):
        if payload is not None:
            break
        if not location:
            raise CollectionError("azure_missing_poll_url")
        try:
            delay = min(max(int(headers.get("Retry-After", "2")), 0), 15)
        except ValueError:
            delay = 2
        sleep(delay)
        payload, headers = _arm(session, token, "GET", location)
        location = headers.get("Location", location)
    if payload is None:
        raise CollectionError("azure_download_pending")
    return payload["url"]


def _pdf(session, url, limit):
    host = _https_host(url)
    if not host or not re.fullmatch(r"[a-z0-9]+\.blob\.core\.windows\.net", host):
        raise CollectionError("azure_invalid_pdf_url")
    try:
        with session.request("GET", url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
            if response.status_code != 200:
                raise CollectionError("azure_pdf_download_failed")
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > limit:
                    raise CollectionError("azure_pdf_too_large")
    except requests.RequestException:
        raise CollectionError("azure_pdf_download_failed") from None
    if not content.startswith(b"%PDF-"):
        raise CollectionError("azure_download_not_pdf")
    return bytes(content)


def _date(value):
    return date.fromisoformat(value[:10]) if value else None


def collect(account, billing_month, dry_run, limit):
    token = get_access_token()
    year, month = map(int, billing_month.split("-"))
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    account_name = quote(account["azure_billing_account"], safe="")
    profile_name = quote(account["azure_billing_profile"], safe="")
    account_url = f"https://{ARM_HOST}/providers/Microsoft.Billing/billingAccounts/{account_name}"
    listing = f"{account_url}/billingProfiles/{profile_name}/invoices"
    params = {"api-version": API_VERSION, "periodStartDate": start.isoformat(), "periodEndDate": end.isoformat()}
    visited = set()
    seen_documents = set()
    with requests.Session() as session:
        session.trust_env = False
        while listing:
            if listing in visited:
                raise CollectionError("azure_repeated_page_url")
            visited.add(listing)
            page, _ = _arm(session, token, "GET", listing, params)
            for invoice in page["value"]:
                properties = invoice["properties"]
                period_start = _date(properties.get("invoicePeriodStartDate"))
                period_end = _date(properties.get("invoicePeriodEndDate"))
                invoice_name = invoice["name"]
                if (not period_start or not period_end or period_end < period_start
                        or not isinstance(invoice_name, str) or not re.fullmatch(r"[A-Za-z0-9-]+", invoice_name)):
                    yield CollectionOutcome(account["id"], "review_required", "azure_invoice_metadata_incomplete")
                    continue
                if not start <= period_start <= end:
                    continue
                documents = properties.get("documents") or [{"kind": properties.get("documentType")}]
                for document in documents:
                    document_name = document.get("name")
                    key = (invoice_name, document_name)
                    if key in seen_documents:
                        continue
                    seen_documents.add(key)
                    if dry_run:
                        yield CollectionOutcome(account["id"], "review_required", "azure_dry_run_document_available")
                        continue
                    kind = DOCUMENT_KINDS.get(document.get("kind"), "unknown")
                    try:
                        url = _download_url(session, token, f"{account_url}/invoices/{invoice_name}", document_name)
                        content = _pdf(session, url, limit)
                        issue_date = _date(properties.get("invoiceDate"))
                        total = properties.get("totalAmount") or {}
                        value, currency = total.get("value"), total.get("currency")
                        include_amount = kind == "invoice" and value is not None and bool(currency)
                        metadata = InvoiceMetadata(
                            issue_date=issue_date.isoformat() if issue_date else None,
                            period_start=period_start.isoformat(), period_end=period_end.isoformat(),
                            invoice_id=invoice_name if kind == "invoice" else None,
                            amount=format(Decimal(str(value)), "f") if include_amount else None,
                            currency=currency if include_amount else None,
                            provenance="azure", verified=kind != "unknown",
                        )
                        source = SourceRef("azure", f"{account_name}:{invoice_name}:{document_name or 'primary'}")
                        yield DocumentCandidate(account["id"], account["vendor_id"], kind, "azure-invoice.pdf",
                                                content, source, metadata, owner_id=account["owner_id"])
                    except CollectionError as error:
                        yield CollectionOutcome(account["id"], error.outcome, error.reason)
            listing = page.get("nextLink")
            params = None
