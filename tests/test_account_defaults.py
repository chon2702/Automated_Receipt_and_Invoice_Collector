"""Compact configuration: account_defaults and the manager template."""

import json
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / 'config' / 'manager.example.json'


def compact(config_file, accounts, defaults=None):
    data = json.loads(config_file.read_text())
    data['owners'].append({'id': 'person2', 'display_name': 'Person Two'})
    data['mailboxes'] = [
        dict(id='va-gmail', address='va@example.test', oauth_client_file='oauth.json', secret_ref='goco-va'),
        dict(id='vl-gmail', address='vl@example.test', oauth_client_file='oauth.json', secret_ref='goco-vl'),
    ]
    if defaults is not None:
        data['account_defaults'] = defaults
    data['accounts'] = accounts
    config_file.write_text(json.dumps(data))
    return data


def resolved(config_file):
    from goco.models.accounts.config import load_config
    return {account['id']: account for account in load_config(config_file).accounts}


DEFAULTS = {'owner_id': 'person1', 'collection': 'gmail', 'mailbox_id': 'va-gmail'}


def test_defaults_fill_owner_collection_and_mailbox(config_file):
    compact(config_file, [{'id': 'github', 'vendor_id': 'github', 'product': 'GitHub'}], DEFAULTS)
    assert resolved(config_file)['github'] == {
        'id': 'github', 'vendor_id': 'github', 'product': 'GitHub',
        'owner_id': 'person1', 'collection': 'gmail', 'mailbox_id': 'va-gmail',
    }


def test_explicit_account_values_win(config_file):
    compact(config_file, [
        {'id': 'shared', 'vendor_id': 'shared', 'product': 'Shared', 'owner_id': 'person2', 'mailbox_id': 'vl-gmail'},
        {'id': 'cloud', 'vendor_id': 'aws', 'product': 'AWS', 'collection': 'aws',
         'aws_profile': 'billing', 'aws_account_id': '123456789012'},
    ], DEFAULTS)
    accounts = resolved(config_file)
    assert (accounts['shared']['owner_id'], accounts['shared']['mailbox_id']) == ('person2', 'vl-gmail')
    assert accounts['cloud']['collection'] == 'aws' and 'mailbox_id' not in accounts['cloud']


def test_manual_account_gets_no_mailbox(config_file):
    compact(config_file, [{'id': 'sample', 'vendor_id': 'sample', 'product': 'Example subscription',
                           'collection': 'manual'}], DEFAULTS)
    assert resolved(config_file)['sample'] == {
        'id': 'sample', 'vendor_id': 'sample', 'product': 'Example subscription',
        'owner_id': 'person1', 'collection': 'manual',
    }


def test_mailbox_default_follows_resolved_collection(config_file):
    defaults = {'owner_id': 'person1', 'collection': 'manual', 'mailbox_id': 'va-gmail'}
    compact(config_file, [
        {'id': 'paper', 'vendor_id': 'paper', 'product': 'Paper'},
        {'id': 'mailed', 'vendor_id': 'mailed', 'product': 'Mailed', 'collection': 'gmail'},
    ], defaults)
    accounts = resolved(config_file)
    assert 'mailbox_id' not in accounts['paper']
    assert accounts['mailed']['mailbox_id'] == 'va-gmail'


def test_existing_configuration_without_defaults_is_unchanged(config_file):
    from goco.models.accounts.config import load_config
    original = json.loads(config_file.read_text())['accounts']
    assert list(load_config(config_file).accounts) == original
    full = dict(id='github', vendor_id='github', product='GitHub', owner_id='person2', collection='gmail', mailbox_id='vl-gmail')
    compact(config_file, [full])
    assert resolved(config_file)['github'] == full


@pytest.mark.parametrize('defaults, accounts, message', [
    ({'owner_id': 'person1', 'product': 'X'}, [], r'account_defaults.product: unknown field'),
    ({'mailbox_id': 'nowhere'}, [], r'account_defaults.mailbox_id: unknown mailbox'),
    ({'owner_id': 'nobody'}, [], r'account_defaults.owner_id: unknown owner'),
    ({'collection': 'imap'}, [], r'account_defaults.collection: must be'),
    ({'owner_id': 'Person One'}, [], r'account_defaults.owner_id: expected lowercase ID'),
    ({'collection': 'gmail', 'mailbox_id': 'va-gmail'}, [{'id': 'a', 'vendor_id': 'a', 'product': 'Vendor'}],
     r'accounts\[0\].owner_id: required'),
    ({'owner_id': 'person1', 'collection': 'gmail'}, [{'id': 'a', 'vendor_id': 'a', 'product': 'Vendor'}],
     r'accounts\[0\].mailbox_id: unknown mailbox'),
    ('not an object', [], r'account_defaults: expected object'),
])
def test_invalid_defaults_and_unresolved_accounts_are_rejected(config_file, defaults, accounts, message):
    from goco.models.accounts.config import ConfigError, load_config
    compact(config_file, accounts, defaults)
    with pytest.raises(ConfigError, match=message):
        load_config(config_file)


def test_manager_template_validates_with_21_compact_vendors(tmp_path):
    from goco.models.accounts.config import load_config
    raw = json.loads(TEMPLATE.read_text(encoding='utf-8'))
    assert set(raw['account_defaults']) == {'owner_id', 'collection', 'mailbox_id'}
    assert len(raw['owners']) == 1 and len(raw['mailboxes']) == 1 and raw['rules'] == []
    assert all(set(account) <= {'id', 'vendor_id', 'product', 'search_aliases'} for account in raw['accounts'])
    copy = tmp_path / 'private' / 'va.json'
    copy.parent.mkdir()
    copy.write_text(TEMPLATE.read_text(encoding='utf-8'), encoding='utf-8')
    config = load_config(copy)
    assert len(config.accounts) == 21
    assert {(a['owner_id'], a['collection'], a['mailbox_id']) for a in config.accounts} == {
        ('manager', 'gmail', 'manager-gmail')}
    assert config.storage_root == copy.parent / 'manager' / 'documents'
