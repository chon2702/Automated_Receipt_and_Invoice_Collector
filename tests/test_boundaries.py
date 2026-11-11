from dataclasses import replace
from datetime import date
import json
import pytest
from test_storage import candidate
from test_expectations import obligation
from test_end_to_end import gmail_config, Client
from test_mime import message,rule
from goco.models import CollectRequest,InvoiceMetadata,parse_month


@pytest.mark.parametrize('field,value', [('owner_id',[]),('product',False),('collection',{}),('vendor_id',False)])
def test_config_rejects_wrong_types_as_input_errors(config_file,field,value):
    from goco.models.accounts.config import load_config,ConfigError
    data=json.loads(config_file.read_text());data['accounts'][0][field]=value
    config_file.write_text(json.dumps(data))
    with pytest.raises(ConfigError): load_config(config_file)


def test_unknown_metadata_prevents_false_missing(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    from goco.models.reporting.expectations import evaluate
    store=DocumentStore(tmp_path/'docs',Index(tmp_path/'index.sqlite3'))
    store.ingest(candidate())
    coverage=[dict(account_id='a',start='2026-11-01',end='2026-12-01',success=True,completed_at='2026-12-02T00:00:00+00:00')]
    assert evaluate([obligation()],store.documents(),date(2026,12,3),coverage)[0]['status']=='unknown'


def test_verification_conflicts_do_not_mutate_existing_docs(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    store=DocumentStore(tmp_path/'docs',Index(tmp_path/'index.sqlite3'))
    first=store.ingest(candidate(invoice_id='INV-1'))
    second=store.ingest(candidate(content=b'%PDF-1.7 other',invoice_id='INV-2'))
    before=(store.root/next(d for d in store.documents() if d.document_id==second.document_id).original_path).parent.joinpath('metadata.json').read_bytes()
    outcome=store.verify(second.document_id,dict(document_type='invoice',period_start='2026-10-01',period_end='2026-10-31',invoice_id='INV-1',reviewed_by='Operator'))
    assert outcome.reason=='business_key_conflict'
    doc=next(d for d in store.documents() if d.document_id==second.document_id)
    assert (store.root/doc.original_path).parent.joinpath('metadata.json').read_bytes()==before


def test_annual_expectation_selection_and_receipt_not_invoice(tmp_path):
    from goco.models.reporting.expectations import select_expectations,evaluate
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    annual=obligation()|dict(period_start='2026-01-16',period_end='2027-01-15')
    assert select_expectations([annual],'2027-02')==[]
    assert len(select_expectations([annual],'2026-07'))==1
    store=DocumentStore(tmp_path/'docs',Index(tmp_path/'index.sqlite3'))
    store.ingest(replace(candidate(),document_type='receipt',metadata=InvoiceMetadata(period_start='2026-10-01',period_end='2026-10-31',verified=True,provenance='manual')))
    assert evaluate([obligation()],store.documents(),date(2026,12,1),[])[0]['status']!='satisfied'


def test_month_boundary_and_multipage_collection(config_file):
    from goco.controller.runner import Runner
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    cfg=gmail_config(config_file)
    window=parse_month('2026-10')
    class Paged(Client):
        def list_messages(self,query,page_token=None):
            if page_token is None: return {'messages':[{'id':'before'},{'id':'start'}],'nextPageToken':'p2'}
            return {'messages':[{'id':'end'},{'id':'last'}]}
        def get_message(self,ident,metadata_only=False):
            time={'before':window.start_epoch-1,'start':window.start_epoch,'end':window.end_epoch,'last':window.end_epoch-1}[ident]
            raw=message(data=b'%PDF-1.7 '+ident.encode(),received=time*1000);raw['id']=ident
            return raw
    store=DocumentStore(cfg.storage_root,Index(cfg.state_dir/'index.sqlite3'))
    result=Runner(cfg,{'mail':Paged()},store).collect(CollectRequest('2026-10',('a',)))
    assert len(store.documents())==2 and result.counts=={'review_required':2}


def test_link_only_and_disabled_rules_visible(config_file):
    from goco.controller.runner import Runner
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    cfg=gmail_config(config_file)
    class Link(Client):
        def get_message(self,*args,**kwargs):
            raw=message();raw['payload'].pop('parts');return raw
    store=DocumentStore(cfg.storage_root,Index(cfg.state_dir/'index.sqlite3'))
    result=Runner(cfg,{'mail':Link()},store).collect(CollectRequest('2026-10',('a',)))
    assert result.counts=={'manual_required':1} and not result.coverage[0]['success']
    data=json.loads(config_file.read_text());data['rules'][0]['enabled']=False
    config_file.write_text(json.dumps(data));from goco.models.accounts.config import load_config
    result=Runner(load_config(config_file),{},store).collect(CollectRequest('2026-10',('a',)))
    assert result.counts=={'manual_required':1}


def test_interrupt_retains_published_original_and_report(config_file):
    from goco.controller.runner import Runner
    from goco.models.persistence.storage import DocumentStore,read_runs
    from goco.models.persistence.index import Index
    cfg=gmail_config(config_file)
    class Interrupted(Client):
        def list_messages(self,query,page_token=None): return {'messages':[{'id':'good'},{'id':'interrupted'}]}
        def get_message(self,ident,metadata_only=False):
            if ident=='interrupted': raise KeyboardInterrupt()
            return message()
    store=DocumentStore(cfg.storage_root,Index(cfg.state_dir/'index.sqlite3'))
    summary=Runner(cfg,{'mail':Interrupted()},store).collect(CollectRequest('2026-10',('a',)))
    assert summary.interrupted and len(store.documents())==1
    assert read_runs(store.root)[0]['interrupted']
    assert not summary.coverage[0]['success']


def test_sidecar_update_failure_keeps_previous_record(tmp_path,monkeypatch):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    import goco.models.persistence.storage as module
    store=DocumentStore(tmp_path/'docs',Index(tmp_path/'index.sqlite3'))
    store.ingest(candidate())
    def fail(*args): raise OSError('no room')
    monkeypatch.setattr(module,'atomic_json',fail)
    with pytest.raises(OSError): store.ingest(candidate(source='two'))
    assert len(store.documents()[0].sources)==1


def test_new_collector_registered_without_runner_changes(monkeypatch):
    from goco.models.integrations.vendors import load_collectors
    import goco.models.integrations.vendors as module
    class Entry:
        name='additional'
        def load(self): return lambda: 'working'
    monkeypatch.setattr(module,'entry_points',lambda **kwargs:[Entry()])
    assert load_collectors()['additional']()=='working'


def test_cli_partial_failure_is_exit_one(config_file,capsys,monkeypatch):
    from goco.view.cli import main
    import goco.controller.runner as module
    from goco.models.integrations.mail.auth import AuthRequired
    gmail_config(config_file)
    def denied(*args,**kwargs): raise AuthRequired('login_required')
    monkeypatch.setattr(module,'make_client',denied)
    assert main(['--config',str(config_file),'collect','--all','--month','2026-10','--format','json'])==1
    data=json.loads(capsys.readouterr().out)
    assert data['counts']=={'auth_required':1}
