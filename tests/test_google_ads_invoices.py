"""Offline tests for the Google Ads invoice collector using synthetic REST responses."""

from dataclasses import asdict
import json

import pytest
import requests

from goco.controller.runner import Runner
from goco.models import CollectRequest, CollectionOutcome, DocumentCandidate
from goco.models.accounts.config import ConfigError, load_config
from goco.models.persistence.index import Index
from goco.models.persistence.storage import DocumentStore

ACCESS_TOKEN = 'ya29.synthetic-access-token'
DEVELOPER_TOKEN = 'synthetic-developer-token'
CUSTOMER_ID = '1234567890'
MANAGER_ID = '9876543210'
SETUP_ID = '5550001'
LIST_URL = f'https://googleads.googleapis.com/v26/customers/{CUSTOMER_ID}/invoices'
PDF_SECRET = 'sig=private-download-secret'
PDF_BYTES = b'%PDF-1.7\n% synthetic Google Ads invoice\n\x00\xff\r\n%%EOF\n'


def pdf_url(name):
    return f'https://payments.google.com/payments/apis-secure/doc/{name}.pdf?{PDF_SECRET}'


def invoice(invoice_id, **changes):
    data = {
        'resourceName': f'customers/{CUSTOMER_ID}/invoices/{invoice_id}',
        'id': invoice_id,
        'type': 'INVOICE',
        'billingSetup': f'customers/{CUSTOMER_ID}/billingSetups/{SETUP_ID}',
        'issueDate': '2026-11-03',
        'dueDate': '2026-12-03',
        'serviceDateRange': {'startDate': '2026-10-01', 'endDate': '2026-10-31'},
        'currencyCode': 'EUR',
        'totalAmountMicros': '1234560000',
        'pdfUrl': pdf_url(invoice_id),
    }
    data.update(changes)
    return data


class Response:
    def __init__(self, status_code, payload=None, body=b'', headers=None, chunks=None):
        self.status_code = status_code
        self.payload = payload
        self.body = body
        self.headers = headers or {}
        self.chunks = chunks

    def json(self, **options):
        if self.payload is None:
            raise ValueError('no json')
        return self.payload

    def iter_content(self, size):
        if self.chunks is not None:
            yield from self.chunks
        else:
            yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeGoogle:
    """Routes requests to synthetic listing and download responses and records every call."""

    def __init__(self, listing, downloads=None):
        self.listing = listing
        self.downloads = downloads or {}
        self.calls = []

    def session(self):
        fake = self

        class Session:
            trust_env = True

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def request(self, method, url, **options):
                fake.calls.append((method, url, options))
                assert options.get('allow_redirects') is False
                assert options.get('timeout')
                if url == LIST_URL:
                    return fake.listing
                result = fake.downloads[url]
                if isinstance(result, Exception):
                    raise result
                return result

        return Session

    @property
    def download_calls(self):
        return [call for call in self.calls if call[1] != LIST_URL]


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv('GOCO_GOOGLE_ADS_ACCESS_TOKEN', ACCESS_TOKEN)
    monkeypatch.setenv('GOCO_GOOGLE_ADS_DEVELOPER_TOKEN', DEVELOPER_TOKEN)


@pytest.fixture
def google_ads():
    from goco.models.integrations.vendors import google_ads
    return google_ads


def account(**changes):
    data = {'id': 'google-ads', 'vendor_id': 'google-ads', 'owner_id': 'person1', 'collection': 'google_ads',
            'google_ads_customer_id': CUSTOMER_ID, 'google_ads_billing_setup_id': SETUP_ID}
    data.update(changes)
    return data


def run(google_ads, monkeypatch, fake, month='2026-11', dry_run=False, limit=1024 * 1024, **account_changes):
    monkeypatch.setattr(google_ads.requests, 'Session', fake.session())
    return list(google_ads.collect(account(**account_changes), month, dry_run, limit))


def assert_no_secrets(items):
    text = repr([asdict(item) for item in items])
    for secret in (ACCESS_TOKEN, DEVELOPER_TOKEN, PDF_SECRET, 'payments.google.com', 'https://'):
        assert secret not in text


def test_lists_by_issue_month_and_downloads_original_once(google_ads, credentials, monkeypatch):
    listing = Response(200, {'invoices': [invoice('3000000001'), invoice('3000000001')]})
    fake = FakeGoogle(listing, {pdf_url('3000000001'): Response(200, body=PDF_BYTES)})

    items = run(google_ads, monkeypatch, fake, google_ads_login_customer_id=MANAGER_ID)

    method, url, options = fake.calls[0]
    assert (method, url) == ('GET', LIST_URL)
    assert options['params'] == {
        'billingSetup': f'customers/{CUSTOMER_ID}/billingSetups/{SETUP_ID}',
        'issueYear': '2026',
        'issueMonth': 'NOVEMBER',
    }
    assert options['headers'] == {
        'Authorization': f'Bearer {ACCESS_TOKEN}',
        'developer-token': DEVELOPER_TOKEN,
        'login-customer-id': MANAGER_ID,
    }
    assert len(fake.download_calls) == 1
    method, url, options = fake.download_calls[0]
    assert (method, url) == ('GET', pdf_url('3000000001'))
    assert options['headers'] == {'Authorization': f'Bearer {ACCESS_TOKEN}'}
    assert options['stream'] is True

    candidate, = items
    assert isinstance(candidate, DocumentCandidate)
    assert candidate.content == PDF_BYTES
    assert candidate.document_type == 'invoice'
    assert candidate.original_filename == 'google-ads-invoice.pdf'
    assert candidate.source.method == 'google_ads'
    assert candidate.source.origin_id == f'{CUSTOMER_ID}:{SETUP_ID}:3000000001'
    metadata = candidate.metadata
    assert (metadata.issue_date, metadata.period_start, metadata.period_end) == ('2026-11-03', '2026-10-01', '2026-10-31')
    assert (metadata.invoice_id, metadata.amount, metadata.currency) == ('3000000001', '1234.56', 'EUR')
    assert metadata.provenance == 'google_ads' and metadata.verified is True
    assert_no_secrets(items)


def test_login_customer_header_is_omitted_without_manager(google_ads, credentials, monkeypatch):
    fake = FakeGoogle(Response(200, {}))
    assert run(google_ads, monkeypatch, fake, month='2026-01') == []
    _, _, options = fake.calls[0]
    assert 'login-customer-id' not in options['headers']
    assert options['params']['issueMonth'] == 'JANUARY'


@pytest.mark.parametrize('micros, expected', [
    ('0', '0'), (0, '0'), ('-1500000', '-1.5'), (-1500000, '-1.5'), ('1', '0.000001'),
    ('100000000', '100'), ('123456789012345678', '123456789012.345678'),
])
def test_micros_convert_exactly(google_ads, micros, expected):
    assert google_ads.micros_to_amount(micros) == expected


@pytest.mark.parametrize('micros', ['1.5', '', '1e6', 1.5, True, None, '١٢٣', ' 1'])
def test_invalid_micros_are_rejected(google_ads, micros):
    with pytest.raises(ValueError):
        google_ads.micros_to_amount(micros)


@pytest.mark.parametrize('kind, amount, document_type', [('CREDIT_MEMO', '-25000000', 'credit_note'), ('INVOICE', '0', 'invoice')])
def test_credit_memo_and_zero_amounts_survive(google_ads, credentials, monkeypatch, kind, amount, document_type):
    listing = Response(200, {'invoices': [invoice('3000000002', type=kind, totalAmountMicros=amount)]})
    fake = FakeGoogle(listing, {pdf_url('3000000002'): Response(200, body=PDF_BYTES)})
    candidate, = run(google_ads, monkeypatch, fake)
    assert candidate.document_type == document_type
    assert candidate.metadata.amount == {'-25000000': '-25', '0': '0'}[amount]
    assert candidate.metadata.verified is True


def test_dry_run_lists_without_downloading(google_ads, credentials, monkeypatch):
    listing = Response(200, {'invoices': [invoice('3000000001'), invoice('3000000001'), invoice('3000000002')]})
    fake = FakeGoogle(listing)
    items = run(google_ads, monkeypatch, fake, dry_run=True)
    assert [(item.outcome, item.reason) for item in items] == [
        ('review_required', 'google_ads_dry_run_invoice_available')] * 2
    assert fake.download_calls == []


@pytest.mark.parametrize('missing', ['GOCO_GOOGLE_ADS_ACCESS_TOKEN', 'GOCO_GOOGLE_ADS_DEVELOPER_TOKEN'])
def test_missing_credentials_require_auth_before_any_request(google_ads, credentials, monkeypatch, missing):
    monkeypatch.delenv(missing)
    fake = FakeGoogle(Response(200, {}))
    with pytest.raises(google_ads.CollectionError) as error:
        run(google_ads, monkeypatch, fake)
    assert (error.value.outcome, error.value.reason) == ('auth_required', 'google_ads_credentials_missing')
    assert fake.calls == []


def test_credentials_with_control_characters_are_rejected(google_ads, credentials, monkeypatch):
    monkeypatch.setenv('GOCO_GOOGLE_ADS_ACCESS_TOKEN', 'token\r\nX-Injected: 1')
    fake = FakeGoogle(Response(200, {}))
    with pytest.raises(google_ads.CollectionError) as error:
        run(google_ads, monkeypatch, fake)
    assert error.value.outcome == 'auth_required'
    assert fake.calls == []


@pytest.mark.parametrize('status', [401, 403])
def test_rejected_listing_credentials_require_auth(google_ads, credentials, monkeypatch, status):
    body = {'error': {'code': status, 'message': f'token {ACCESS_TOKEN} rejected', 'status': 'UNAUTHENTICATED'}}
    fake = FakeGoogle(Response(status, body))
    with pytest.raises(google_ads.CollectionError) as error:
        run(google_ads, monkeypatch, fake)
    assert (error.value.outcome, error.value.reason) == ('auth_required', 'google_ads_access_denied')
    assert ACCESS_TOKEN not in str(error.value)


def test_ineligible_account_falls_back_to_manual(google_ads, credentials, monkeypatch):
    body = {'error': {'code': 400, 'status': 'INVALID_ARGUMENT', 'details': [{
        '@type': 'type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure',
        'errors': [{'errorCode': {'invoiceError': 'NOT_INVOICED_CUSTOMER'}, 'message': 'Not invoiced.'}],
    }]}}
    fake = FakeGoogle(Response(400, body))
    with pytest.raises(google_ads.CollectionError) as error:
        run(google_ads, monkeypatch, fake)
    assert (error.value.outcome, error.value.reason) == ('manual_required', 'google_ads_not_invoiced_customer')


@pytest.mark.parametrize('response', [Response(500, {}), Response(400, None, body=b'not json'), Response(302, None, headers={'Location': 'https://example.test/'})])
def test_other_listing_failures_are_safe(google_ads, credentials, monkeypatch, response):
    with pytest.raises(google_ads.CollectionError) as error:
        run(google_ads, monkeypatch, FakeGoogle(response))
    assert (error.value.outcome, error.value.reason) == ('failed', 'google_ads_request_failed')


def test_listing_transport_error_does_not_leak(google_ads, credentials, monkeypatch):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, *args, **kwargs):
            raise requests.ConnectionError(f'{LIST_URL} Authorization: Bearer {ACCESS_TOKEN}')

    monkeypatch.setattr(google_ads.requests, 'Session', Session)
    with pytest.raises(google_ads.CollectionError) as error:
        list(google_ads.collect(account(), '2026-11', False, 1024))
    assert error.value.reason == 'google_ads_request_failed'
    assert error.value.__cause__ is None and error.value.__suppress_context__


@pytest.mark.parametrize('payload', [{}, {'invoices': []}])
def test_empty_listing_yields_nothing(google_ads, credentials, monkeypatch, payload):
    fake = FakeGoogle(Response(200, payload))
    assert run(google_ads, monkeypatch, fake) == []
    assert fake.download_calls == []


def test_unknown_type_and_missing_metadata_stay_unverified(google_ads, credentials, monkeypatch):
    partial = invoice('3000000004')
    del partial['serviceDateRange'], partial['totalAmountMicros']
    malformed = invoice('3000000005', type=['INVOICE'], currencyCode=7, serviceDateRange='October')
    listing = Response(200, {'invoices': [invoice('3000000003', type='SOMETHING_NEW'), partial, malformed]})
    fake = FakeGoogle(listing, {pdf_url('3000000003'): Response(200, body=PDF_BYTES),
                                pdf_url('3000000004'): Response(200, body=PDF_BYTES + b'2'),
                                pdf_url('3000000005'): Response(200, body=PDF_BYTES + b'3')})
    unknown, incomplete, odd = run(google_ads, monkeypatch, fake)
    assert unknown.document_type == 'unknown' and unknown.metadata.verified is False
    assert odd.document_type == 'unknown' and odd.metadata.verified is False
    assert (odd.metadata.period_start, odd.metadata.amount, odd.metadata.currency) == (None, None, None)
    assert incomplete.document_type == 'invoice' and incomplete.metadata.verified is False
    assert incomplete.metadata.issue_date == '2026-11-03'
    assert (incomplete.metadata.period_start, incomplete.metadata.amount, incomplete.metadata.currency) == (None, None, None)


def test_per_invoice_failures_are_isolated_and_redacted(google_ads, credentials, monkeypatch):
    lookalike = 'https://payments.google.com.attacker.test/doc.pdf?' + PDF_SECRET
    rows = [
        invoice('4000000001'),
        invoice('4000000002'),  # not a PDF
        invoice('4000000003'),  # oversized stream
        invoice('4000000004'),  # redirect
        invoice('4000000005', pdfUrl='https://evil.example/doc.pdf?' + PDF_SECRET),
        invoice('4000000006', pdfUrl=lookalike),
        invoice('4000000007', pdfUrl='https://user:pass@payments.google.com/doc.pdf'),
        invoice('4000000008', pdfUrl='http://payments.google.com/doc.pdf'),
        invoice('4000000009', pdfUrl='https://payments.google.com:8443/doc.pdf'),
        invoice('4000000010', pdfUrl=None),
        invoice('4000000011'),  # 403 on download
        invoice('4000000012'),  # transport error carrying the URL
        invoice('../escape'),
        invoice('4000000013', issueDate='not-a-date'),
        invoice('4000000014'),
    ]
    downloads = {
        pdf_url('4000000001'): Response(200, body=PDF_BYTES),
        pdf_url('4000000002'): Response(200, body=b'<html>sign in</html>'),
        pdf_url('4000000003'): Response(200, chunks=[b'%PDF-1.7 ' + b'x' * 600, b'y' * 600]),
        pdf_url('4000000004'): Response(302, headers={'Location': 'https://accounts.google.com/?' + PDF_SECRET}),
        pdf_url('4000000011'): Response(403),
        pdf_url('4000000012'): requests.ConnectionError(pdf_url('4000000012')),
        pdf_url('4000000013'): Response(200, body=PDF_BYTES + b'undated'),
        pdf_url('4000000014'): Response(200, body=PDF_BYTES + b'last'),
    }
    fake = FakeGoogle(Response(200, {'invoices': rows}), downloads)

    items = run(google_ads, monkeypatch, fake, limit=1000)

    summary = [(type(item).__name__, getattr(item, 'outcome', None), getattr(item, 'reason', None)) for item in items]
    assert summary == [
        ('DocumentCandidate', None, None),
        ('CollectionOutcome', 'failed', 'google_ads_download_not_pdf'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_too_large'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_redirect_rejected'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_host_rejected'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_host_rejected'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_host_rejected'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_host_rejected'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_host_rejected'),
        ('CollectionOutcome', 'review_required', 'google_ads_pdf_unavailable'),
        ('CollectionOutcome', 'auth_required', 'google_ads_pdf_access_denied'),
        ('CollectionOutcome', 'failed', 'google_ads_pdf_download_failed'),
        ('CollectionOutcome', 'review_required', 'google_ads_invoice_metadata_incomplete'),
        ('DocumentCandidate', None, None),
        ('DocumentCandidate', None, None),
    ]
    assert items[0].content == PDF_BYTES and items[-1].content == PDF_BYTES + b'last'
    undated = items[-2]
    assert undated.metadata.issue_date is None and undated.metadata.verified is False
    requested = {url for _, url, _ in fake.download_calls}
    assert requested == set(downloads)
    for _, url, options in fake.download_calls:
        assert url.startswith('https://payments.google.com/')
        assert options['headers'] == {'Authorization': f'Bearer {ACCESS_TOKEN}'}
    assert_no_secrets(items)


@pytest.mark.parametrize('url', [
    'https://payments.google.com/doc.pdf',
    'https://PAYMENTS.google.com/doc.pdf',
])
def test_approved_host_check_accepts_exact_host(google_ads, url):
    assert google_ads.approved_pdf_url(url)


@pytest.mark.parametrize('url', [
    None, 42, '', 'payments.google.com/doc.pdf', 'https://payments.google.com./doc.pdf',
    'https://xpayments.google.com/doc.pdf', 'https://google.com/doc.pdf', 'https://payments.google.com:443@evil.test/',
    'https://payments.google.com\\@evil.test/', 'https://payments.google.com/doc.pdf#frag',
    'https://payments.google.com /doc.pdf', 'https://[::1]/doc.pdf', 'https://payments.google.com:abc/',
])
def test_approved_host_check_rejects_everything_else(google_ads, url):
    assert not google_ads.approved_pdf_url(url)


# Runner, configuration, command and report integration.

def configure(config_file, **changes):
    data = json.loads(config_file.read_text())
    entry = account(product='Google Ads')
    entry.update(changes)
    data['accounts'].append(entry)
    config_file.write_text(json.dumps(data))
    return data


def test_runner_dry_run_collect_and_duplicate_rerun(config_file, credentials, google_ads, monkeypatch):
    configure(config_file, google_ads_login_customer_id=MANAGER_ID)
    config = load_config(config_file)
    store = DocumentStore(config.storage_root, Index(config.state_dir / 'index.sqlite3'), config.max_bytes)
    listing = Response(200, {'invoices': [invoice('3000000001')]})
    fake = FakeGoogle(listing, {pdf_url('3000000001'): Response(200, body=PDF_BYTES)})
    monkeypatch.setattr(google_ads.requests, 'Session', fake.session())
    runner = Runner(config, {}, store)

    summary = runner.collect(CollectRequest('2026-11', ('google-ads',), True))
    assert summary.counts == {'review_required': 1}
    assert fake.download_calls == [] and not store.root.exists()

    first = runner.collect(CollectRequest('2026-11', ('google-ads',)))
    assert first.counts == {'saved': 1}
    second = runner.collect(CollectRequest('2026-11', ('google-ads',)))
    assert second.counts == {'duplicate': 1}
    assert second.outcomes[0]['document_id'] == first.outcomes[0]['document_id']

    document, = store.documents()
    assert (store.root / document.original_path).read_bytes() == PDF_BYTES
    assert document.original_path.startswith('documents/google-ads/google-ads/2026-11/')
    assert document.metadata['issue_date'] == '2026-11-03'
    assert (document.metadata['period_start'], document.metadata['period_end']) == ('2026-10-01', '2026-10-31')
    assert document.metadata['amount'] == '1234.56'
    assert len(document.sources) == 1
    written = ''.join(path.read_text(encoding='utf-8') for path in store.root.rglob('*.json'))
    for secret in (ACCESS_TOKEN, DEVELOPER_TOKEN, PDF_SECRET, 'payments.google.com'):
        assert secret not in written


def test_runner_reports_missing_credentials_as_auth_required(config_file, google_ads, monkeypatch):
    monkeypatch.delenv('GOCO_GOOGLE_ADS_ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('GOCO_GOOGLE_ADS_DEVELOPER_TOKEN', raising=False)
    configure(config_file)
    config = load_config(config_file)
    store = DocumentStore(config.storage_root, Index(config.state_dir / 'index.sqlite3'))
    summary = Runner(config, {}, store).collect(CollectRequest('2026-11', ('google-ads',), True))
    assert summary.outcomes == [asdict(CollectionOutcome('google-ads', 'auth_required', 'google_ads_credentials_missing'))]


def test_cli_dry_run_json_and_exit_codes(config_file, credentials, google_ads, monkeypatch, capsys):
    from goco.view.cli import main

    configure(config_file)
    fake = FakeGoogle(Response(200, {'invoices': [invoice('3000000001')]}))
    monkeypatch.setattr(google_ads.requests, 'Session', fake.session())
    arguments = ['--config', str(config_file), 'collect', '--account', 'google-ads', '--month', '2026-11', '--dry-run', '--format', 'json']
    assert main(arguments) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['counts'] == {'review_required': 1}
    assert not (config_file.parent / 'documents').exists()

    monkeypatch.delenv('GOCO_GOOGLE_ADS_ACCESS_TOKEN')
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)['counts'] == {'auth_required': 1}


def test_gmail_coverage_cannot_certify_google_ads_account(config_file, credentials):
    from goco.controller import dispatch
    from goco.controller.commands import Status

    data = configure(config_file)
    data['expectations'].append({
        'id': 'google-ads-october', 'account_id': 'google-ads', 'required_type': 'invoice',
        'period_start': '2026-10-01', 'period_end': '2026-10-31', 'due_date': '2026-10-31', 'grace_days': 0,
        'minimum_count': 1, 'discovery_start': '2026-10-01', 'discovery_end': '2026-10-31',
    })
    config_file.write_text(json.dumps(data))
    run_id = '00000000-0000-4000-8000-000000000001'
    runs = config_file.parent / 'documents' / 'runs'
    runs.mkdir(parents=True)
    (runs / f'{run_id}.json').write_text(json.dumps({
        'schema_version': 1, 'run_id': run_id, 'started_at': '2026-11-02T00:00:00+00:00',
        'completed_at': '2026-11-02T00:00:00+00:00', 'outcomes': [], 'coverage': [{
            'account_id': 'google-ads', 'mailbox_id': 'old-mailbox', 'start': '2026-10-01', 'end': '2026-11-01',
            'success': True, 'completed_at': '2026-11-02T00:00:00+00:00',
        }],
    }))
    result = dispatch(config_file, Status('2026-10'))
    assert result.error is None
    obligation, = result.data['obligations']
    assert obligation['status'] == 'unknown' and obligation['coverage_incomplete'] is True


@pytest.mark.parametrize('changes, field', [
    ({'google_ads_customer_id': '123-456-7890'}, 'google_ads_customer_id'),
    ({'google_ads_customer_id': '123456789'}, 'google_ads_customer_id'),
    ({'google_ads_customer_id': 1234567890}, 'google_ads_customer_id'),
    ({'google_ads_customer_id': '１２３４５６７８９０'}, 'google_ads_customer_id'),
    ({'google_ads_billing_setup_id': ''}, 'google_ads_billing_setup_id'),
    ({'google_ads_billing_setup_id': 'abc'}, 'google_ads_billing_setup_id'),
    ({'google_ads_billing_setup_id': '12/34'}, 'google_ads_billing_setup_id'),
    ({'google_ads_login_customer_id': '12345678901'}, 'google_ads_login_customer_id'),
    ({'google_ads_login_customer_id': ''}, 'google_ads_login_customer_id'),
])
def test_config_rejects_invalid_google_ads_ids(config_file, changes, field):
    configure(config_file, **changes)
    with pytest.raises(ConfigError, match=rf'{field}: expected'):
        load_config(config_file)


@pytest.mark.parametrize('key', ['google_ads_customer_id', 'google_ads_billing_setup_id', 'google_ads_login_customer_id'])
@pytest.mark.parametrize('collection', ['manual', 'azure'])
def test_config_rejects_google_ads_fields_on_other_collectors(config_file, key, collection):
    data = json.loads(config_file.read_text())
    data['accounts'][0].update(collection=collection, **{key: '1234567890'})
    if collection == 'azure':
        data['accounts'][0].update(azure_billing_account='12345', azure_billing_profile='example')
    config_file.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match='Google Ads settings require google_ads collection'):
        load_config(config_file)


def test_config_requires_google_ads_ids_for_google_ads_collection(config_file):
    data = configure(config_file)
    del data['accounts'][-1]['google_ads_billing_setup_id']
    config_file.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match='google_ads_billing_setup_id: expected'):
        load_config(config_file)
