import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@pytest.fixture
def config_file(tmp_path):
    data = {
        'schema_version': 1, 'storage_root': 'documents', 'state_dir': 'state',
        'owners': [{'id': 'person1', 'display_name': 'Person One'}],
        'mailboxes': [], 'accounts': [{'id': 'sample', 'vendor_id': 'sample',
        'product': 'Annual subscription', 'owner_id': 'person1', 'collection': 'manual'}],
        'rules': [], 'expectations': [],
    }
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path
