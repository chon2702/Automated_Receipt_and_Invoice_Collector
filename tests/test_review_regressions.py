from datetime import datetime,timezone,date
import json
import sqlite3
import pytest
from test_end_to_end import gmail_config,Client
from test_mime import message
from test_expectations import obligation
from goco.models import CollectRequest


def setup(config_file):
    from goco.runner import Runner
    from goco.storage import DocumentStore
    from goco.index import Index
    cfg=gmail_config(config_file)
    store=DocumentStore(cfg.storage_root,Index(cfg.state_dir/'index.sqlite3'))
    return cfg,store


@pytest.mark.parametrize('month',['2026-11','2026-12'])
def test_scan_cannot_certify_unelapsed_window(config_file,month,monkeypatch):
    from goco.runner import Runner
    from goco.expectations import evaluate
    cfg,store=setup(config_file)
    class Empty(Client):
        def list_messages(self,*args): return {}
    import goco.runner as module
    monkeypatch.setattr(module,'utcnow',lambda:'2026-11-05T12:00:00+00:00')
    summary=Runner(cfg,{'mail':Empty()},store).collect(CollectRequest(month,('a',)))
    assert all(c['end']<='2026-11-05' or not c['success'] for c in summary.coverage)
    assert evaluate([obligation()],[],date(2026,11,8),summary.coverage)[0]['status']=='unknown'


def test_sqlite_failure_records_failed_coverage_and_keeps_original(config_file,monkeypatch):
    from goco.runner import Runner
    from goco.storage import read_runs
    cfg,store=setup(config_file)
    def fail(document): raise sqlite3.OperationalError('secret database detail')
    monkeypatch.setattr(store.index,'upsert',fail)
    summary=Runner(cfg,{'mail':Client()},store).collect(CollectRequest('2026-10',('a',)))
    assert summary.counts=={'failed':1}
    assert not summary.coverage[0]['success']
    assert len(store.documents())==1
    assert read_runs(store.root)[0]['coverage'][0]['success'] is False


def test_disabling_rules_supersedes_prior_success(config_file):
    from goco.runner import Runner
    from goco.storage import read_runs
    from goco.config import load_config
    from goco.expectations import covered
    cfg,store=setup(config_file)
    class Empty(Client):
        def list_messages(self,*args): return {}
    first=Runner(cfg,{'mail':Empty()},store).collect(CollectRequest('2026-10',('a',)))
    assert first.coverage[0]['success']
    data=json.loads(config_file.read_text());data['rules'][0]['enabled']=False
    config_file.write_text(json.dumps(data))
    second=Runner(load_config(config_file),{},store).collect(CollectRequest('2026-10',('a',)))
    assert not second.coverage[0]['success']
    e=obligation()|{'discovery_start':'2026-10-01','discovery_end':'2026-11-01'}
    assert not covered(e,first.coverage+second.coverage)


def test_link_only_review_retains_private_source_and_safe_identifier(config_file):
    from goco.runner import Runner
    cfg,store=setup(config_file)
    class Link(Client):
        def get_message(self,*args,**kwargs):
            raw=message();raw['id']='private-message-id';raw['payload'].pop('parts');return raw
    result=Runner(cfg,{'mail':Link()},store).collect(CollectRequest('2026-10',('a',)))
    outcome=result.outcomes[0]
    assert 'review_id' in outcome and outcome['review_id']
    assert 'private-message-id' not in json.dumps(result.outcomes)
    review=json.loads((store.root/'review'/(outcome['review_id']+'.json')).read_text())
    assert review['source']['message_id']=='private-message-id'
    assert review['source']['mailbox_id']=='mail'
