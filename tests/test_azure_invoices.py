import json

import pytest

from goco.controller.runner import Runner
from goco.models import CollectRequest
from goco.models.accounts.config import load_config
from goco.models.persistence.index import Index
from goco.models.persistence.storage import DocumentStore


def test_azure_dry_run_and_collection_use_billing_period(config_file, monkeypatch):
    from goco.models.integrations.vendors import azure

    data = json.loads(config_file.read_text())
    account = data['accounts'][0]
    account.update(collection='azure', azure_billing_account='12345', azure_billing_profile='example')
    config_file.write_text(json.dumps(data))
    config = load_config(config_file)
    store = DocumentStore(config.storage_root, Index(config.state_dir / 'index.sqlite3'))
    next_link = 'https://management.azure.com/next-page'
    signed = 'https://billing.blob.core.windows.net/invoices/original.pdf?sig=private'
    invoice = {'name': 'G123456789', 'properties': {
        'invoiceDate': '2026-11-04T00:00:00Z',
        'invoicePeriodStartDate': '2026-10-01T00:00:00Z',
        'invoicePeriodEndDate': '2026-10-31T00:00:00Z',
        'documents': [{'name': 'original', 'kind': 'Invoice'}],
    }}
    calls = []
    token = 'fake-token'

    class Response:
        def __init__(self, code, payload=None, headers=None):
            self.status_code = code
            self.payload = payload
            self.headers = headers or {}

        def json(self, **options):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b'%PDF-1.7 original Azure invoice'

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, **options):
            calls.append((method, url, options))
            assert options['allow_redirects'] is False
            if url == signed:
                assert 'Authorization' not in options.get('headers', {})
                return Response(200)
            assert options['headers']['Authorization'] == 'Bearer ' + token
            if url == next_link:
                return Response(200, {'value': [invoice]})
            if url.endswith('/operationResults/example'):
                return Response(200, {'url': signed})
            if method == 'POST':
                assert options['params']['documentName'] == 'original'
                return Response(202, headers={'Location': 'https://management.azure.com/operationResults/example', 'Retry-After': '0'})
            assert options['params']['periodStartDate'] == '2026-10-01'
            assert options['params']['periodEndDate'] == '2026-10-31'
            return Response(200, {'value': [], 'nextLink': next_link})

    monkeypatch.setattr(azure, 'get_access_token', lambda: token)
    monkeypatch.setattr(azure.requests, 'Session', Session)
    runner = Runner(config, {}, store)
    assert runner.collect(CollectRequest('2026-10', (account['id'],), True)).counts == {'review_required': 1}
    assert len(calls) == 2 and not store.root.exists()
    request = CollectRequest('2026-10', (account['id'],))
    assert runner.collect(request).counts == {'saved': 1}
    assert runner.collect(request).counts == {'duplicate': 1}
    document, = store.documents()
    assert document.metadata['issue_date'] == '2026-11-04'
    assert document.metadata['period_end'] == '2026-10-31'
    assert (store.root / document.original_path).read_bytes() == b'%PDF-1.7 original Azure invoice'
    assert 'sig=private' not in repr(document.sources)


def test_azure_rejects_external_pagination_before_sending_token():
    from goco.models.integrations.vendors import azure

    class Session:
        def request(self, *args, **kwargs):
            pytest.fail('Token must stay on the Azure management host')

    with pytest.raises(azure.CollectionError, match='^azure_invalid_billing_url$'):
        azure._arm(Session(), 'test-token', 'GET', 'https://management.azure.com.attacker.test/next')
