import json
from datetime import datetime, timezone

import pytest


def test_config_paths_are_relative_to_file(config_file, monkeypatch, tmp_path):
    from goco.models.accounts.config import load_config
    monkeypatch.chdir(tmp_path.parent)
    cfg = load_config(config_file)
    assert cfg.storage_root == config_file.parent / 'documents'
    assert not cfg.storage_root.exists()


@pytest.mark.parametrize('change, field', [
    (lambda d: d['accounts'][0].update(owner_id='absent'), 'owner_id'),
    (lambda d: d['owners'].append(d['owners'][0]), 'owners'),
    (lambda d: d.update(typo=True), 'typo'),
    (lambda d: d.update(state_dir='documents/state'), 'state_dir'),
    (lambda d: d['accounts'][0].update(id='../bad'), 'id'),
    (lambda d: d.update(schema_version=True), 'schema_version'),
])
def test_invalid_config_has_no_side_effects(config_file, change, field):
    from goco.models.accounts.config import ConfigError, load_config
    data = json.loads(config_file.read_text())
    change(data)
    config_file.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match=field):
        load_config(config_file)
    assert not (config_file.parent / 'documents').exists()
    assert not (config_file.parent / 'state').exists()


def test_berlin_month_boundaries():
    from goco.models import parse_month
    window = parse_month('2026-10')
    assert window.start_epoch == int(datetime(2026, 9, 30, 22, tzinfo=timezone.utc).timestamp())
    assert window.end_epoch == int(datetime(2026, 10, 31, 23, tzinfo=timezone.utc).timestamp())
    with pytest.raises(ValueError):
        parse_month('2026-13')
