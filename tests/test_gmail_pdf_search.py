"""Name-based Gmail PDF search: configuration, search, per-PDF decisions and CLI output."""

import base64
import json

import pytest

from goco.models import CollectRequest


MAILBOX = dict(id='mail', address='finance@example.test', oauth_client_file='oauth.json', secret_ref='mail')


def gmail_account(account_id, product, aliases=None, **changes):
    account = dict(id=account_id, vendor_id=account_id, product=product, owner_id='person1',
                   collection='gmail', mailbox_id='mail')
    if aliases:
        account['search_aliases'] = aliases
    account.update(changes)
    return account


def link_rule(account_id='google-ads-link', **changes):
    rule = dict(id=account_id + '-notice', account_id=account_id, purpose='link_notice',
                sender_addresses=['ads-noreply@google.com'], subject_contains='billing documents are available',
                attachment_extensions=['.pdf'], document_type='invoice', enabled=True)
    rule.update(changes)
    return rule


def write_config(config_file, accounts, rules=()):
    data = json.loads(config_file.read_text())
    data['mailboxes'] = [MAILBOX]
    data['accounts'] = list(accounts)
    data['rules'] = list(rules)
    config_file.write_text(json.dumps(data))
    return data


def load(config_file):
    from goco.models.accounts.config import load_config
    return load_config(config_file)


# Step 1: configuration and explicit migration.

def test_vendor_needs_only_account_mailbox_and_search_names(config_file):
    write_config(config_file, [gmail_account('anthropic', 'Claude', ['Anthropic'])])
    config = load(config_file)
    assert config.accounts[0]['search_aliases'] == ['Anthropic']


def test_legacy_attachment_rule_fails_with_migration_hint(config_file):
    legacy = link_rule('anthropic')
    del legacy['purpose']
    write_config(config_file, [gmail_account('anthropic', 'Claude')], [legacy])
    from goco.models.accounts.config import ConfigError
    with pytest.raises(ConfigError, match=r'rules\[0\]: rules no longer collect PDF attachments.*link_notice'):
        load(config_file)


def test_link_notice_rule_is_kept_next_to_name_search(config_file):
    write_config(config_file, [gmail_account('anthropic', 'Claude'), gmail_account('google-ads-link', 'Google Ads notices')],
                 [link_rule()])
    assert load(config_file).rules[0]['purpose'] == 'link_notice'


@pytest.mark.parametrize('accounts, rules, message', [
    ([gmail_account('a', 'Vendor')], [link_rule('a', purpose='attachments')], r'purpose: must be link_notice'),
    ([gmail_account('a', 'Vendor', ['Alias'])], [link_rule('a')], r'link_notice accounts do not use the PDF name search'),
    ([dict(gmail_account('a', 'Vendor', ['Alias']), collection='manual')], [], r'search aliases require gmail'),
    ([gmail_account('a', 'Vendor', ['say "hi"'])], [], r'search_aliases: expected a vendor search name'),
    ([gmail_account('a', 'Vendor', ['x'])], [], r'search_aliases: expected a vendor search name'),
    ([gmail_account('a', 'Vendor', ['a\\b'])], [], r'search_aliases: expected a vendor search name'),
    ([gmail_account('a', 'Vendor', ['VENDOR'])], [], r'duplicate search name'),
    ([gmail_account('a', 'Vendor', search_aliases=[])], [], r'search_aliases: expected nonempty array'),
    ([gmail_account('a', '"Quoted"')], [], r'accounts\[0\].product: expected a vendor search name'),
])
def test_invalid_search_configuration_is_rejected(config_file, accounts, rules, message):
    from goco.models.accounts.config import ConfigError
    write_config(config_file, accounts, rules)
    with pytest.raises(ConfigError, match=message):
        load(config_file)


# Step 2: Gmail search in the model.

def b64(data):
    return base64.urlsafe_b64encode(data).decode()


def pdf_part(filename, data=b'%PDF-1.7 synthetic', part_id=None, attachment_id=None):
    part = {'filename': filename, 'mimeType': 'application/pdf', 'partId': part_id or filename,
            'body': {'size': len(data)}}
    if attachment_id:
        part['body']['attachmentId'] = attachment_id
    else:
        part['body']['data'] = b64(data)
    return part


def raw_message(ident, sender, subject, attachments, text='', html=None, received=1792000000000):
    body = [{'mimeType': 'text/plain', 'partId': 't', 'body': {'data': b64(text.encode()), 'size': len(text)}}]
    if html is not None:
        body = [{'mimeType': 'text/html', 'partId': 'h', 'body': {'data': b64(html.encode()), 'size': len(html)}}]
    return {'id': ident, 'internalDate': str(received), 'payload': {
        'mimeType': 'multipart/mixed',
        'headers': [{'name': 'From', 'value': sender}, {'name': 'To', 'value': 'finance@example.test'},
                    {'name': 'Subject', 'value': subject}],
        'parts': [{'mimeType': 'multipart/alternative', 'parts': body}, *attachments],
    }}


ACCOUNTS = [
    gmail_account('anthropic-claude', 'Claude', ['Anthropic']),
    gmail_account('voiceflow', 'Voiceflow'),
    gmail_account('webflow', 'Webflow'),
    gmail_account('make-celonis', 'Make / Celonis', ['Make.com', 'Celonis']),
]

ANTHROPIC = raw_message(
    'claude-oct', 'Anthropic, PBC <invoice+statements@mail.anthropic.com>', 'Your receipt from Anthropic, PBC #2955-7394-3842',
    [pdf_part('Invoice-6XL5CWJJ-0008.pdf', b'%PDF-1.7 invoice', attachment_id='att-invoice'),
     pdf_part('Receipt-2955-7394-3842.pdf', b'%PDF-1.7 receipt', attachment_id='att-receipt')],
    text='Thanks for your payment. Make sure to keep this receipt.')
STRIPE = raw_message(
    'stripe', 'Stripe <invoice+statements@stripe.com>', 'Your receipt #1234-5678',
    [pdf_part('Invoice-4F2A.pdf', b'%PDF-1.7 stripe invoice')],
    html='<p>Receipt from <b>Voiceflow</b> Inc.</p><style>.webflow{}</style>')
SUPPORT = raw_message(
    'support', 'Anthropic Support <support@anthropic.com>', 'Re: Your question about API usage',
    [pdf_part('usage-report.pdf', b'%PDF-1.7 report')], text='Hi, the usage report you asked for is attached.')
AMBIGUOUS = raw_message(
    'ambiguous', 'Billing Agent <billing@agent.example>', 'Invoices for Voiceflow and Webflow',
    [pdf_part('invoice-october.pdf', b'%PDF-1.7 combined')])


def decisions(raw, accounts=ACCOUNTS):
    from goco.models.integrations.mail.mime import normalize_message
    from goco.models.integrations.mail.search import decide_attachments
    return [(d.part['filename'], d.account_ids, d.document_type, d.reason)
            for d in decide_attachments(normalize_message(raw), accounts)]


def test_queries_bundle_all_names_per_mailbox():
    from goco.models.integrations.mail.search import search_names, search_queries
    names = [name for account in ACCOUNTS for name in search_names(account)]
    query, = search_queries(1790805600, 1793487600, names)
    assert query.startswith('after:1790805599 before:1793487600 has:attachment filename:pdf {')
    for term in ('"Claude"', '"Anthropic"', 'filename:anthropic', '"Make / Celonis"', '"Make.com"', '"Voiceflow"'):
        assert term in query
    assert 'filename:make.com' not in query


def test_long_name_lists_are_split_into_several_queries():
    from goco.models.integrations.mail.search import search_queries
    names = [f'Vendor{index:03d}' for index in range(120)] + ['vendor000']
    queries = search_queries(10, 20, names, limit=200)
    assert len(queries) > 1 and all(len(q.split('{', 1)[1]) <= 202 for q in queries)
    joined = ' '.join(queries)
    assert all(f'"Vendor{index:03d}"' in joined for index in range(120))
    assert joined.casefold().count('vendor000') == 2  # one phrase and one filename term, duplicate name dropped


def test_invoice_and_receipt_in_one_message_get_their_own_types():
    assert decisions(ANTHROPIC) == [
        ('Invoice-6XL5CWJJ-0008.pdf', ('anthropic-claude',), 'invoice', 'matched'),
        ('Receipt-2955-7394-3842.pdf', ('anthropic-claude',), 'receipt', 'matched'),
    ]


def test_stripe_mail_is_assigned_from_text_when_headers_name_no_vendor():
    # "webflow" only occurs inside a <style> block, which is not message text.
    assert decisions(STRIPE) == [('Invoice-4F2A.pdf', ('voiceflow',), 'invoice', 'matched')]


@pytest.mark.parametrize('subject, filename', [
    ('Your Voiceflow receipt', 'receipt-1.pdf'),
    ('Your receipt', 'Voiceflow-receipt-1.pdf'),
])
def test_stripe_mail_is_assigned_from_subject_or_file_name(subject, filename):
    raw = raw_message('s', 'Stripe <receipts@stripe.com>', subject, [pdf_part(filename)], text='Webflow sponsored this newsletter.')
    assert decisions(raw) == [(filename, ('voiceflow',), 'receipt', 'matched')]


def test_vendor_name_alone_is_not_an_invoice():
    assert decisions(SUPPORT) == [('usage-report.pdf', ('anthropic-claude',), None, 'no_invoice_feature')]


def test_unclear_document_type_is_reviewed():
    raw = raw_message('u', 'Voiceflow <billing@voiceflow.com>', 'Your invoice', [pdf_part('2026-10.pdf')])
    combined = raw_message('c', 'Voiceflow <billing@voiceflow.com>', 'Documents', [pdf_part('Invoice-and-Receipt.pdf')])
    assert decisions(raw) == [('2026-10.pdf', ('voiceflow',), None, 'document_type_unclear')]
    assert decisions(combined) == [('Invoice-and-Receipt.pdf', ('voiceflow',), None, 'document_type_unclear')]


def test_ambiguous_vendor_is_reviewed_and_file_name_can_resolve_it():
    assert decisions(AMBIGUOUS) == [('invoice-october.pdf', ('voiceflow', 'webflow'), 'invoice', 'ambiguous_vendor')]
    resolved = raw_message('r', 'Billing Agent <billing@agent.example>', 'Invoices for Voiceflow and Webflow',
                           [pdf_part('webflow-invoice.pdf'), pdf_part('voiceflow-invoice.pdf')])
    assert [d[1] for d in decisions(resolved)] == [('webflow',), ('voiceflow',)]


@pytest.mark.parametrize('text', [
    'Claudette sent the invoice', 'see anthropical.example', 'Please make a payment',
])
def test_names_match_whole_words_only(text):
    raw = raw_message('w', 'Someone <a@b.example>', 'Invoice', [pdf_part('invoice.pdf')], text=text)
    assert decisions(raw) == []


def test_name_matching_handles_case_domains_and_separators():
    raw = raw_message('d', 'billing@make.com', 'Rechnung Nr. 5', [pdf_part('RECHNUNG_5.PDF')])
    assert decisions(raw) == [('RECHNUNG_5.PDF', ('make-celonis',), 'invoice', 'matched')]
    german = raw_message('g', 'ANTHROPIC <x@y.example>', 'Zahlungsbestätigung', [pdf_part('Zahlungsbeleg-7.pdf'), pdf_part('logo.png')])
    assert decisions(german) == [('Zahlungsbeleg-7.pdf', ('anthropic-claude',), 'receipt', 'matched')]


# Steps 3 and 4: per-PDF decisions, storage, orchestration and CLI output.

LINK_NOTICE = raw_message(
    'ads-notice', 'Google Ads <ads-noreply@google.com>', 'Your Google Ads billing documents are available', [],
    text='Download your invoice in the billing portal.')
UNRELATED = raw_message('other', 'Someone <a@b.example>', 'Invoice', [pdf_part('invoice.pdf', b'%PDF-1.7 other')])


class FakeGmail:
    """Pages of two, the same hits for every query, and a record of every call."""

    def __init__(self, messages, fail=None):
        self.messages = {raw['id']: raw for raw in messages}
        self.fail = fail
        self.queries, self.fetched, self.downloads = [], [], []

    def list_messages(self, query, page_token=None):
        if self.fail:
            raise self.fail
        if page_token is None:
            self.queries.append(query)
        ids = sorted(self.messages)
        start = int(page_token or 0)
        page = {'messages': [{'id': ident} for ident in ids[start:start + 2]]}
        if start + 2 < len(ids):
            page['nextPageToken'] = str(start + 2)
        return page

    def get_message(self, ident, metadata_only=False):
        self.fetched.append((ident, metadata_only))
        raw = json.loads(json.dumps(self.messages[ident]))
        if metadata_only:
            raw['payload'].pop('parts', None)
        return raw

    def get_attachment(self, message_id, attachment_id):
        self.downloads.append((message_id, attachment_id))
        for part in self.messages[message_id]['payload']['parts']:
            if part.get('body', {}).get('attachmentId') == attachment_id:
                return b'%PDF-1.7 ' + part['filename'].split('-')[0].lower().encode()
        raise AssertionError('unknown attachment')


SCENARIO = [ANTHROPIC, STRIPE, SUPPORT, AMBIGUOUS, UNRELATED, LINK_NOTICE]


def scenario_runner(config_file, client, link_account=True):
    from goco.controller.runner import Runner
    from goco.models.persistence.index import Index
    from goco.models.persistence.storage import DocumentStore
    accounts = list(ACCOUNTS)
    rules = []
    if link_account:
        accounts.append(gmail_account('google-ads-link', 'Google Ads portal notices'))
        rules.append(link_rule())
    write_config(config_file, accounts, rules)
    config = load(config_file)
    store = DocumentStore(config.storage_root, Index(config.state_dir / 'index.sqlite3'))
    return Runner(config, {'mail': client}, store), store


def results(summary):
    return {row['account_id']: {key: value for key, value in row.items() if key != 'account_id' and value}
            for row in summary.account_results}


def test_collect_saves_each_pdf_with_its_type_and_reruns_as_duplicates(config_file, monkeypatch):
    import goco.controller.runner as runner_module
    from goco.models.integrations.mail import search
    # Force several bundled queries so overlapping hits must be merged by message ID.
    monkeypatch.setattr(runner_module, 'search_queries',
                        lambda start, end, names: search.search_queries(start, end, names, limit=60))
    client = FakeGmail(SCENARIO)
    runner, store = scenario_runner(config_file, client)
    selected = ('anthropic-claude', 'voiceflow', 'webflow', 'make-celonis', 'google-ads-link')

    first = runner.collect(CollectRequest('2026-10', selected))

    name_queries = [q for q in client.queries if 'has:attachment' in q]
    assert len(name_queries) > 1 and any('from:ads-noreply@google.com' in q for q in client.queries)
    fetched = [ident for ident, _ in client.fetched]
    assert sorted(fetched) == sorted(set(fetched)) == sorted(m['id'] for m in SCENARIO)
    assert results(first) == {
        'anthropic-claude': {'status': 'completed', 'saved': 2, 'metadata_review': 2, 'skipped': 1},
        'voiceflow': {'status': 'completed', 'saved': 1, 'metadata_review': 1, 'review': 1},
        'webflow': {'status': 'completed', 'review': 1},
        'make-celonis': {'status': 'no_matches'},
        'google-ads-link': {'status': 'completed', 'manual': 1},
    }
    documents = {(d.account_id, d.document_type, d.original_filename): d for d in store.documents()}
    assert set(documents) == {
        ('anthropic-claude', 'invoice', 'Invoice-6XL5CWJJ-0008.pdf'),
        ('anthropic-claude', 'receipt', 'Receipt-2955-7394-3842.pdf'),
        ('voiceflow', 'invoice', 'Invoice-4F2A.pdf'),
    }
    receipt = documents[('anthropic-claude', 'receipt', 'Receipt-2955-7394-3842.pdf')]
    assert (store.root / receipt.original_path).read_bytes() == b'%PDF-1.7 receipt'
    assert receipt.sources[0]['message_id'] == 'claude-oct' and receipt.sources[0]['attachment_id'] == 'att-receipt'
    assert not [d for d in client.downloads if d[0] in ('support', 'ambiguous', 'other')]

    reviews = {(o['account_id'], o['reason']): o['review_id'] for o in first.outcomes if o['review_id']}
    assert set(reviews) == {('voiceflow', 'ambiguous_vendor'), ('webflow', 'ambiguous_vendor'),
                            ('google-ads-link', 'no_supported_attachment')}
    review = json.loads((store.root / 'review' / (reviews[('webflow', 'ambiguous_vendor')] + '.json')).read_text())
    assert review['source']['message_id'] == 'ambiguous' and review['source']['attachment_id'] == 'invoice-october.pdf'
    coverage = {c['account_id']: c['success'] for c in first.coverage}
    assert coverage == {'anthropic-claude': True, 'voiceflow': False, 'webflow': False,
                        'make-celonis': True, 'google-ads-link': False}

    second = runner.collect(CollectRequest('2026-10', selected))
    assert results(second)['anthropic-claude'] == {'status': 'completed', 'duplicate': 2, 'skipped': 1}
    assert results(second)['voiceflow'] == {'status': 'completed', 'duplicate': 1, 'review': 1}
    assert len(store.documents()) == 3


def test_selected_account_query_uses_its_names_but_detects_ambiguity_with_all(config_file):
    client = FakeGmail([AMBIGUOUS, ANTHROPIC])
    runner, store = scenario_runner(config_file, client, link_account=False)
    summary = runner.collect(CollectRequest('2026-10', ('voiceflow',)))
    query, = client.queries
    assert '"Voiceflow"' in query and 'Webflow' not in query and 'Claude' not in query
    assert [(o['account_id'], o['reason']) for o in summary.outcomes] == [('voiceflow', 'ambiguous_vendor')]
    assert client.downloads == [] and store.documents() == []


def test_dry_run_reports_per_pdf_without_downloading_or_writing(config_file):
    client = FakeGmail(SCENARIO)
    runner, store = scenario_runner(config_file, client)
    summary = runner.collect(CollectRequest('2026-10', ('anthropic-claude', 'voiceflow', 'webflow'), True))
    assert client.downloads == []
    assert all(metadata_only is False for _, metadata_only in client.fetched)
    assert not store.root.exists()
    assert sorted((o['account_id'], o['reason']) for o in summary.outcomes) == [
        ('anthropic-claude', 'dry_run_potential_invoice'), ('anthropic-claude', 'dry_run_potential_receipt'),
        ('anthropic-claude', 'no_invoice_feature'), ('voiceflow', 'ambiguous_vendor'),
        ('voiceflow', 'dry_run_potential_invoice'), ('webflow', 'ambiguous_vendor'),
    ]
    assert not any(o['review_id'] for o in summary.outcomes)


def test_inline_pdf_and_invalid_pdf_bytes(config_file):
    inline = raw_message('inline', 'Webflow <billing@webflow.com>', 'Receipt',
                         [pdf_part('receipt.pdf', b'%PDF-1.7 inline'), pdf_part('invoice.pdf', b'<html>not a pdf</html>')])
    client = FakeGmail([inline])
    runner, store = scenario_runner(config_file, client, link_account=False)
    summary = runner.collect(CollectRequest('2026-10', ('webflow',)))
    assert sorted((o['outcome'], o['reason']) for o in summary.outcomes) == [
        ('review_required', 'invalid_document'), ('review_required', 'metadata_unverified')]
    assert [d.document_type for d in store.documents()] == ['receipt']


def run_cli(config_file, capsys, *extra):
    from goco.view.cli import main
    code = main(['--config', str(config_file), 'collect', '--month', '2026-10', *extra])
    return code, capsys.readouterr().out


def test_cli_shows_per_vendor_results_and_separates_no_matches_from_failure(config_file, capsys, monkeypatch):
    import goco.controller.runner as runner_module
    from goco.models.integrations.mail.gmail import TransportError
    scenario_runner(config_file, FakeGmail([]), link_account=False)
    clients = iter([FakeGmail(SCENARIO), FakeGmail([]), FakeGmail([], fail=TransportError('gmail_access_denied'))])
    monkeypatch.setattr(runner_module, 'make_client', lambda mailbox, dry_run=False: next(clients))

    code, out = run_cli(config_file, capsys, '--all')
    assert code == 0
    lines = out.splitlines()
    assert 'anthropic-claude | ok | saved=2 (metadata review 2) duplicate=0 review=0 skipped=1' in lines
    assert 'webflow | ok | saved=0 (metadata review 0) duplicate=0 review=1 skipped=0' in lines
    assert 'make-celonis | ok, no matches | saved=0 (metadata review 0) duplicate=0 review=0 skipped=0' in lines

    code, out = run_cli(config_file, capsys, '--account', 'make-celonis', '--format', 'json')
    assert code == 0
    assert json.loads(out)['accounts'] == [dict(account_id='make-celonis', status='no_matches', reason=None, saved=0,
                                                metadata_review=0, duplicate=0, review=0, skipped=0, manual=0)]

    code, out = run_cli(config_file, capsys, '--account', 'make-celonis')
    assert code == 1
    assert 'make-celonis | FAILED (gmail_access_denied) | saved=0 (metadata review 0) duplicate=0 review=0 skipped=0' in out.splitlines()
