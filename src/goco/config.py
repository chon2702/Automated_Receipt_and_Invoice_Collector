import json
import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .models import AppConfig, DOCUMENT_TYPES, MAX_BYTES


class ConfigError(ValueError):
    pass


def _fail(path, message):
    raise ConfigError(f"{path}: {message}")


def _object(value, required, optional, path):
    if not isinstance(value, dict):
        _fail(path, "expected object")
    for key in value.keys() - set(required) - set(optional):
        _fail(f"{path}.{key}", "unknown field")
    for key in required:
        if key not in value:
            _fail(f"{path}.{key}", "required")


def slug(value, path):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        _fail(path, "expected lowercase ID, maximum 64 characters")


def _text(value, path):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        _fail(path, "expected nonempty text without control characters")


def _date(value, path):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError()
        return date.fromisoformat(value)
    except ValueError:
        _fail(path, "expected YYYY-MM-DD")


def _pairs(obj, left, right, path):
    if (left in obj) != (right in obj):
        _fail(path, f"{left} and {right} must be provided together")
    if left in obj and _date(obj[left], path + '.' + left) > _date(obj[right], path + '.' + right):
        _fail(path, "date range is reversed")


def load_config(path: Path) -> AppConfig:
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        _fail("config", "cannot read valid JSON")
    root_keys = ["schema_version", "storage_root", "state_dir", "owners", "mailboxes", "accounts", "rules", "expectations"]
    _object(data, root_keys, ["timezone", "max_bytes"], "config")
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        _fail('schema_version', 'must be 1')
    for name in ('storage_root', 'state_dir'):
        _text(data[name], name)
    storage = (path.parent / data['storage_root']).resolve()
    state = (path.parent / data['state_dir']).resolve()
    if storage == state or storage in state.parents or state in storage.parents:
        _fail('state_dir', 'must not overlap storage_root')
    tz = data.get('timezone', 'Europe/Berlin')
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        _fail('timezone', 'unknown timezone')
    maximum = data.get('max_bytes', MAX_BYTES)
    if type(maximum) is not int or not 1 <= maximum <= MAX_BYTES:
        _fail('max_bytes', 'must be between 1 and 26214400')
    shapes = {
      'owners': (['id', 'display_name'], []),
      'mailboxes': (['id', 'address', 'oauth_client_file', 'secret_ref'], []),
      'accounts': (['id','vendor_id','product','owner_id','collection'], ['mailbox_id','external_account_label','card_folder_label','active_from','active_until','aws_profile','aws_account_id']),
      'rules': (['id','account_id','sender_addresses','attachment_extensions','document_type','enabled'], ['subject_contains','recipient_addresses']),
      'expectations': (['id','account_id','required_type','period_start','period_end','due_date','grace_days','minimum_count'], ['discovery_start','discovery_end']),
    }
    ids = {}
    for group, (required, optional) in shapes.items():
        rows = data[group]
        if not isinstance(rows, list):
            _fail(group, 'expected array')
        ids[group] = set()
        for i, row in enumerate(rows):
            where = f'{group}[{i}]'
            _object(row, required, optional, where)
            for key in ('owner_id','account_id','mailbox_id'):
                if key in row: slug(row[key], where + '.' + key)
            for key in ('subject_contains','external_account_label','card_folder_label'):
                if key in row: _text(row[key], where + '.' + key)
            slug(row['id'], where + '.id')
            if row['id'] in ids[group]:
                _fail(group, 'duplicate ID')
            ids[group].add(row['id'])
            for name, value in row.items():
                if isinstance(value, str):
                    _text(value, where + '.' + name)
            if group == 'owners':
                _text(row['display_name'], where + '.display_name')
            if group == 'mailboxes':
                for key in ('address', 'oauth_client_file', 'secret_ref'):
                    _text(row[key], where + '.' + key)
                if not re.fullmatch(r'[^\s@]+@[^\s@]+', row['address']):
                    _fail(where + '.address', 'invalid address')
                row['oauth_client_file'] = str((path.parent / row['oauth_client_file']).resolve())
                if Path(row['oauth_client_file']).is_relative_to(storage):
                    _fail(where + '.oauth_client_file', 'must be outside shared storage')
            if group == 'accounts':
                slug(row['vendor_id'], where + '.vendor_id')
                _text(row['product'], where + '.product')
                if row['collection'] not in ('gmail', 'manual', 'aws'):
                    _fail(where + '.collection', 'must be gmail, manual, or aws')
                if row['collection'] == 'aws':
                    _text(row.get('aws_profile'), where + '.aws_profile')
                    aws_id = row.get('aws_account_id')
                    if not isinstance(aws_id, str) or not re.fullmatch(r'[0-9]{12}', aws_id):
                        _fail(where + '.aws_account_id', 'expected a 12-digit AWS account ID')
                elif 'aws_profile' in row or 'aws_account_id' in row:
                    _fail(where, 'AWS settings require aws collection')
                for key in ('active_from','active_until'):
                    if key in row: _date(row[key], where + '.' + key)
                if row.get('active_from', '0001-01-01') > row.get('active_until', '9999-12-31'):
                    _fail(where, 'active dates reversed')
            if group == 'rules':
                if not isinstance(row['document_type'],str) or row['document_type'] not in DOCUMENT_TYPES: _fail(where, 'invalid document_type')
                if type(row['enabled']) is not bool: _fail(where, 'enabled must be boolean')
                for key in ('sender_addresses','attachment_extensions','recipient_addresses'):
                    if key not in row: continue
                    if not isinstance(row[key], list) or not row[key]: _fail(where + '.' + key, 'expected nonempty array')
                    for item in row[key]: _text(item, where + '.' + key)
                if any(ext not in ('.pdf','.xml') for ext in row['attachment_extensions']):
                    _fail(where, 'unsupported attachment extension')
                for key in ('sender_addresses','recipient_addresses'):
                    for address in row.get(key, []):
                        if not re.fullmatch(r'[^\s@]+@[^\s@]+', address): _fail(where, 'invalid address')
            if group == 'expectations':
                if not isinstance(row['required_type'],str) or row['required_type'] not in DOCUMENT_TYPES - {'unknown'}: _fail(where, 'invalid required_type')
                _pairs(row, 'period_start','period_end', where)
                _date(row['due_date'], where + '.due_date')
                for key, minimum in [('grace_days',0), ('minimum_count',1)]:
                    if type(row[key]) is not int or row[key] < minimum: _fail(where + '.' + key, 'invalid integer')
                _pairs(row,'discovery_start','discovery_end',where)
                if row.get('discovery_start') == row.get('discovery_end') and 'discovery_start' in row: _fail(where, 'empty discovery window')
    for i, account in enumerate(data['accounts']):
        if account['owner_id'] not in ids['owners']: _fail(f'accounts[{i}].owner_id', 'unknown owner')
        if account['collection'] == 'gmail' and account.get('mailbox_id') not in ids['mailboxes']:
            _fail(f'accounts[{i}].mailbox_id', 'unknown mailbox')
        if 'mailbox_id' in account and account['mailbox_id'] not in ids['mailboxes']: _fail('mailbox_id','unknown mailbox')
    for group in ('rules','expectations'):
        for row in data[group]:
            if row['account_id'] not in ids['accounts']: _fail(group + '.account_id','unknown account')
    accounts = {a['id']: a for a in data['accounts']}
    for row in data['expectations']:
        account = accounts[row['account_id']]
        if row['period_start'] < account.get('active_from','0001-01-01') or row['period_end'] > account.get('active_until','9999-12-31'):
            _fail('expectations', 'period outside account active dates')
    if len({m['secret_ref'] for m in data['mailboxes']}) != len(data['mailboxes']): _fail('mailboxes', 'secret_ref must be unique')
    return AppConfig(path, storage, state, *(tuple(data[g]) for g in shapes), tz, maximum)
