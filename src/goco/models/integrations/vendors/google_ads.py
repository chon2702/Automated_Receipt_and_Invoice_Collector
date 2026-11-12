"""Collect original Google Ads invoice PDFs for accounts on monthly invoicing.

Invoices are listed by issue month. The service period is kept separately in the
saved metadata because an invoice issued in November usually covers October.
"""

from datetime import date
from decimal import Decimal
import os
import re
from urllib.parse import urlsplit

import requests

from goco.models import CollectionOutcome, DocumentCandidate, InvoiceMetadata, SourceRef
from .errors import CollectionError

API_VERSION = "v26"
API_HOST = "googleads.googleapis.com"
# Provisional: confirm against a real invoice during the pilot before adding hosts.
APPROVED_PDF_HOSTS = frozenset({"payments.google.com"})
ACCESS_TOKEN_ENV = "GOCO_GOOGLE_ADS_ACCESS_TOKEN"
DEVELOPER_TOKEN_ENV = "GOCO_GOOGLE_ADS_DEVELOPER_TOKEN"
TIMEOUT = (10, 30)
MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
          "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")
DOCUMENT_KINDS = {"INVOICE": "invoice", "CREDIT_MEMO": "credit_note"}
MANUAL_ERRORS = {"NOT_INVOICED_CUSTOMER": "google_ads_not_invoiced_customer"}


def _credential(name):
    value = os.environ.get(name, "")
    if not value or not value.isascii() or not value.isprintable() or any(c.isspace() for c in value):
        raise CollectionError("google_ads_credentials_missing", "auth_required")
    return value


def credentials():
    """Read the pilot credentials from the process environment."""
    return _credential(ACCESS_TOKEN_ENV), _credential(DEVELOPER_TOKEN_ENV)


def approved_pdf_url(url):
    """Return True only for an HTTPS URL on an exact approved host and default port."""
    if not isinstance(url, str) or not url or not url.isascii() or not url.isprintable():
        return False
    if any(char.isspace() or char == "\\" for char in url):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.fragment
                and parsed.hostname in APPROVED_PDF_HOSTS)
    except ValueError:
        return False


def micros_to_amount(micros):
    """Convert integer micros (JSON int64 may arrive as a string) to an exact decimal string."""
    if type(micros) is int:
        value = micros
    elif isinstance(micros, str) and re.fullmatch(r"-?[0-9]+", micros):
        value = int(micros)
    else:
        raise ValueError("invalid_micros")
    return format(Decimal(value).scaleb(-6).normalize(), "f")


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _error_codes(response):
    try:
        body = response.json()
        details = body["error"].get("details") or []
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()
    codes = set()
    for detail in details if isinstance(details, list) else []:
        for item in (detail.get("errors") or []) if isinstance(detail, dict) else []:
            error_code = item.get("errorCode") if isinstance(item, dict) else None
            if isinstance(error_code, dict):
                codes.update(value for value in error_code.values() if isinstance(value, str))
    return codes


def _list(session, headers, customer_id, setup_id, billing_month):
    year, month = map(int, billing_month.split("-"))
    url = f"https://{API_HOST}/{API_VERSION}/customers/{customer_id}/invoices"
    params = {
        "billingSetup": f"customers/{customer_id}/billingSetups/{setup_id}",
        "issueYear": str(year),
        "issueMonth": MONTHS[month - 1],
    }
    try:
        with session.request("GET", url, params=params, headers=headers,
                             timeout=TIMEOUT, allow_redirects=False) as response:
            if response.status_code == 200:
                payload = response.json()
            else:
                for code in _error_codes(response):
                    if code in MANUAL_ERRORS:
                        raise CollectionError(MANUAL_ERRORS[code], "manual_required")
                if response.status_code in (401, 403):
                    raise CollectionError("google_ads_access_denied", "auth_required")
                raise CollectionError("google_ads_request_failed")
    except (requests.RequestException, ValueError):
        # Transport errors can echo URLs or headers.
        raise CollectionError("google_ads_request_failed") from None
    if not isinstance(payload, dict):
        raise CollectionError("google_ads_request_failed")
    invoices = payload.get("invoices", [])
    if not isinstance(invoices, list):
        raise CollectionError("google_ads_request_failed")
    return invoices


def _download(session, access_token, url, limit):
    if not approved_pdf_url(url):
        raise CollectionError("google_ads_pdf_host_rejected")
    try:
        with session.request("GET", url, headers={"Authorization": f"Bearer {access_token}"}, stream=True,
                             timeout=TIMEOUT, allow_redirects=False) as response:
            if 300 <= response.status_code < 400:
                raise CollectionError("google_ads_pdf_redirect_rejected")
            if response.status_code in (401, 403):
                raise CollectionError("google_ads_pdf_access_denied", "auth_required")
            if response.status_code != 200:
                raise CollectionError("google_ads_pdf_download_failed")
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > limit:
                    raise CollectionError("google_ads_pdf_too_large")
    except requests.RequestException:
        raise CollectionError("google_ads_pdf_download_failed") from None
    if not content.startswith(b"%PDF-"):
        raise CollectionError("google_ads_download_not_pdf")
    return bytes(content)


def _metadata(invoice, kind):
    issue_date = _date(invoice.get("issueDate"))
    service = invoice.get("serviceDateRange")
    service = service if isinstance(service, dict) else {}
    start, end = _date(service.get("startDate")), _date(service.get("endDate"))
    if not start or not end or end < start:
        start = end = None
    currency = invoice.get("currencyCode")
    try:
        amount = micros_to_amount(invoice.get("totalAmountMicros"))
    except ValueError:
        amount = None
    if amount is None or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        amount = currency = None
    return InvoiceMetadata(
        issue_date=issue_date.isoformat() if issue_date else None,
        period_start=start.isoformat() if start else None,
        period_end=end.isoformat() if end else None,
        invoice_id=invoice["id"],
        amount=amount, currency=currency, provenance="google_ads",
        verified=bool(kind != "unknown" and issue_date and start and amount is not None),
    )


def collect(account, billing_month, dry_run, limit):
    access_token, developer_token = credentials()
    customer_id = account["google_ads_customer_id"]
    setup_id = account["google_ads_billing_setup_id"]
    headers = {"Authorization": f"Bearer {access_token}", "developer-token": developer_token}
    if account.get("google_ads_login_customer_id"):
        headers["login-customer-id"] = account["google_ads_login_customer_id"]
    seen = set()
    with requests.Session() as session:
        session.trust_env = False
        for invoice in _list(session, headers, customer_id, setup_id, billing_month):
            invoice_id = invoice.get("id") if isinstance(invoice, dict) else None
            if not isinstance(invoice_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", invoice_id):
                yield CollectionOutcome(account["id"], "review_required", "google_ads_invoice_metadata_incomplete")
                continue
            if invoice_id in seen:
                continue
            seen.add(invoice_id)
            if dry_run:
                yield CollectionOutcome(account["id"], "review_required", "google_ads_dry_run_invoice_available")
                continue
            try:
                url = invoice.get("pdfUrl")
                if not url:
                    raise CollectionError("google_ads_pdf_unavailable", "review_required")
                content = _download(session, access_token, url, limit)
                invoice_type = invoice.get("type")
                kind = DOCUMENT_KINDS.get(invoice_type, "unknown") if isinstance(invoice_type, str) else "unknown"
                yield DocumentCandidate(
                    account["id"], account["vendor_id"], kind, "google-ads-invoice.pdf", content,
                    SourceRef("google_ads", f"{customer_id}:{setup_id}:{invoice_id}"),
                    _metadata(invoice, kind), owner_id=account["owner_id"],
                )
            except CollectionError as error:
                yield CollectionOutcome(account["id"], error.outcome, error.reason)
