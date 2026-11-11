import json

import pytest
from datetime import datetime, timezone

from goco.config import load_config
from goco.index import Index
from goco.models import CollectRequest
from goco.runner import Runner
from goco.storage import DocumentStore


def test_aws_collection_paginates_and_preserves_originals(config_file, monkeypatch):
    from goco.vendors import aws

    data = json.loads(config_file.read_text())
    account = data["accounts"][0]
    account.update(collection="aws", aws_profile="billing", aws_account_id="123456789012")
    config_file.write_text(json.dumps(data))
    config = load_config(config_file)
    store = DocumentStore(config.storage_root, Index(config.state_dir / "index.sqlite3"))
    calls = []

    class Client:
        def list_invoice_summaries(self, **kwargs):
            calls.append(kwargs)
            invoice_id = "second" if kwargs.get("NextToken") else "first"
            page = {"InvoiceSummaries": [{
                "InvoiceId": invoice_id, "AccountId": "123456789012",
                "BillingPeriod": {"Year": 2026, "Month": 10},
                "InvoiceType": "INVOICE",
                "IssuedDate": datetime(2026, 11, 3, tzinfo=timezone.utc),
            }]}
            if invoice_id == "first":
                page["NextToken"] = "page2"
            return page

        def get_invoice_pdf(self, *, InvoiceId):
            return {"InvoicePDF": {"InvoiceId": InvoiceId, "DocumentUrl": InvoiceId}}

    monkeypatch.setattr(aws, "make_client", lambda profile: Client())
    monkeypatch.setattr(aws, "download_pdf", lambda url, limit: b"%PDF-1.7 " + url.encode())
    runner = Runner(config, {}, store)
    request = CollectRequest("2026-10", (account["id"],))
    assert runner.collect(request).counts == {"saved": 2}
    assert calls[0]["Filter"]["BillingPeriod"] == {"Year": 2026, "Month": 10}
    assert calls[1]["NextToken"] == "page2"
    assert runner.collect(request).counts == {"duplicate": 2}
    documents = store.documents()
    assert {doc.metadata["issue_date"] for doc in documents} == {"2026-11-03"}
    assert {doc.metadata["period_end"] for doc in documents} == {"2026-10-31"}
    assert {doc.sources[0]["method"] for doc in documents} == {"aws"}
    assert {(store.root / doc.original_path).read_bytes() for doc in documents} == {
        b"%PDF-1.7 first", b"%PDF-1.7 second",
    }


def test_aws_sdk_dry_run_lists_billing_month_without_fetching_pdf(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber
    from goco.vendors import aws

    client = boto3.client("invoicing", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    account = {"id": "billing", "aws_profile": "billing", "aws_account_id": "123456789012"}
    period = {"Year": 2026, "Month": 10}
    monkeypatch.setattr(aws, "make_client", lambda profile: client)
    with Stubber(client) as stub:
        stub.add_response("list_invoice_summaries", {"InvoiceSummaries": [{
            "InvoiceId": "example", "AccountId": account["aws_account_id"], "BillingPeriod": period,
        }]}, {"Selector": {"ResourceType": "ACCOUNT_ID", "Value": account["aws_account_id"]},
              "Filter": {"BillingPeriod": period}, "MaxResults": 100})
        result = list(aws.collect(account, "2026-10", True, 1000))
        assert [item.reason for item in result] == ["aws_dry_run_invoice_available"]
        stub.assert_no_pending_responses()


def test_aws_refreshes_expired_pdf_url_and_preserves_primary_when_supplement_fails(monkeypatch):
    from goco.vendors import aws

    account = {"id": "billing", "aws_account_id": "123456789012", "vendor_id": "aws", "owner_id": "test"}
    urls = iter(["expired", "fresh"])

    class Client:
        def get_invoice_pdf(self, *, InvoiceId):
            return {"InvoicePDF": {"InvoiceId": InvoiceId, "DocumentUrl": next(urls),
                                  "SupplementalDocuments": [{"DocumentId": "tax", "DocumentUrl": "bad"}]}}

    def download(url, limit):
        if url == "expired":
            raise aws.DownloadForbidden()
        if url == "bad":
            raise aws.CollectionError("aws_pdf_download_failed")
        assert url == "fresh"
        return b"%PDF-1.7 original"

    monkeypatch.setattr(aws, "download_pdf", download)
    documents = list(aws._invoice_documents(Client(), account,
                     {"InvoiceId": "example", "InvoiceType": "INVOICE"}, 2026, 10, 1000))
    assert documents[0].content == b"%PDF-1.7 original"
    assert documents[1].reason == "aws_pdf_download_failed"


@pytest.mark.parametrize("url", [
    "http://bucket.s3.us-east-1.amazonaws.com/private?token=secret",
    "https://bucket.s3.us-east-1.amazonaws.com.attacker.test/private?token=secret",
    "https://user@bucket.s3.us-east-1.amazonaws.com/private?token=secret",
])
def test_aws_rejects_unsafe_pdf_urls_without_echoing_them(url):
    from goco.vendors import aws

    with pytest.raises(aws.CollectionError, match="^aws_invalid_pdf_url$"):
        aws.download_pdf(url, 1000)
