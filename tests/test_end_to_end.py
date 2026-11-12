import json
from dataclasses import replace
from pathlib import Path
from test_mime import message, rule
from goco.models import CollectRequest


def gmail_config(config_file):
    """Account 'a' finds PDF attachments by its product name, without rules."""
    from goco.models.accounts.config import load_config
    data=json.loads(config_file.read_text())
    data['mailboxes']=[dict(id='mail',address='finance@example.test',oauth_client_file='oauth.json',secret_ref='mail')]
    data['accounts'][0].update(id='a',product='Example Vendor',collection='gmail',mailbox_id='mail')
    data['rules']=[]
    config_file.write_text(json.dumps(data))
    return load_config(config_file)


def link_notice_config(config_file):
    """Account 'a' keeps an explicit link-notice rule, like the Google Ads notification path."""
    from goco.models.accounts.config import load_config
    gmail_config(config_file)
    data=json.loads(config_file.read_text())
    data['rules']=[rule()]
    config_file.write_text(json.dumps(data))
    return load_config(config_file)


class Client:
    def list_messages(self,query,page_token=None): return {'messages':[{'id':'m1'}]}
    def get_message(self,ident,metadata_only=False):
        raw=message()
        if metadata_only: raw['payload'].pop('parts')
        return raw
    def get_attachment(self,*args): raise AssertionError('unexpected attachment fetch')


def test_rerun_and_dry_run_no_mutation(config_file):
    from goco.controller.runner import Runner
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    cfg=gmail_config(config_file)
    store=DocumentStore(cfg.storage_root,Index(cfg.state_dir/'index.sqlite3'))
    class FilteredClient(Client):
        def list_messages(self, query, page_token=None):
            assert 'has:attachment filename:pdf' in query and '"Example Vendor"' in query
            return super().list_messages(query, page_token)

    runner=Runner(cfg,{'mail':FilteredClient()},store)
    dry=runner.collect(CollectRequest('2026-10',('a',),True))
    assert not cfg.storage_root.exists() and not cfg.state_dir.exists()
    first=runner.collect(CollectRequest('2026-10',('a',)))
    assert first.counts=={'review_required':1}
    second=runner.collect(CollectRequest('2026-10',('a',)))
    assert second.counts=={'duplicate':1}
    before={str(p):p.read_bytes() for root in (cfg.storage_root,cfg.state_dir) for p in root.rglob('*') if p.is_file()}
    runner.collect(CollectRequest('2026-10',('a',),True))
    after={str(p):p.read_bytes() for root in (cfg.storage_root,cfg.state_dir) for p in root.rglob('*') if p.is_file()}
    assert before==after


def test_failed_mailbox_retains_success_and_no_complete_coverage(config_file):
    from goco.controller.runner import Runner
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    from goco.models.integrations.mail.auth import AuthRequired
    cfg=gmail_config(config_file)
    data=json.loads(config_file.read_text())
    data['mailboxes'].append(dict(id='bad',address='bad@example.test',oauth_client_file='oauth2.json',secret_ref='bad'))
    data['accounts'].append(dict(id='b',vendor_id='vendor',owner_id='person1',product='other',collection='gmail',mailbox_id='bad'))
    config_file.write_text(json.dumps(data))
    from goco.models.accounts.config import load_config
    cfg=load_config(config_file)
    class Bad:
        def list_messages(self,*args): raise AuthRequired('auth_required')
    store=DocumentStore(cfg.storage_root,Index(cfg.state_dir/'index.sqlite3'))
    summary=Runner(cfg,{'mail':Client(),'bad':Bad()},store).collect(CollectRequest('2026-10',('a','b')))
    assert summary.counts=={'review_required':1,'auth_required':1}
    assert len(store.documents())==1
    assert not next(c for c in summary.coverage if c['account_id']=='b')['success']


def test_cli_import_verify_status_rebuild(config_file,tmp_path,capsys):
    from goco.view.cli import main
    file=tmp_path/'invoice.pdf';file.write_bytes(b'%PDF-1.7 original')
    base=['--config',str(config_file)]
    assert main(base+['import',str(file),'--account','sample','--type','invoice'])==0
    payload=json.loads(capsys.readouterr().out)
    ident=payload['document_id']
    assert main(base+['document','verify','--id',ident,'--type','invoice','--period-start','2026-10-01','--period-end','2026-10-31','--reviewed-by','Operator'])==0
    capsys.readouterr()
    assert main(base+['status','--month','2026-10','--format','json'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['accounts'][0]['status']=='unknown'
    assert main(base+['index','rebuild'])==0
