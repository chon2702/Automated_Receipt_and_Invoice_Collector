from datetime import date
from dataclasses import replace
import pytest
from test_storage import candidate
from goco.models import InvoiceMetadata


def obligation():
    return dict(id='oct', account_id='a', required_type='invoice', period_start='2026-10-01', period_end='2026-10-31', due_date='2026-11-05', grace_days=2, minimum_count=1, discovery_start='2026-11-01', discovery_end='2026-12-01')


def test_pending_missing_unknown_and_failed_coverage(tmp_path):
    from goco.models.reporting.expectations import evaluate
    e=obligation()
    assert evaluate([e], [], date(2026,11,7), [])[0]['status']=='pending'
    assert evaluate([e], [], date(2026,11,8), [])[0]['status']=='unknown'
    coverage=[dict(account_id='a', start='2026-11-01', end='2026-12-01', success=True, completed_at='2026-12-02T00:00:00+00:00')]
    assert evaluate([e], [], date(2026,12,2), coverage)[0]['status']=='missing'
    coverage.append(dict(account_id='a',start='2026-11-10',end='2026-11-11',success=False,completed_at='2026-12-03T00:00:00+00:00'))
    result=evaluate([e],[],date(2026,12,3),coverage)[0]
    assert result['status']=='unknown' and result['coverage_incomplete']
    coverage.append(dict(account_id='a',start='2026-11-01',end='2026-12-01',success=True,completed_at='2026-12-04T00:00:00+00:00'))
    assert evaluate([e],[],date(2026,12,4),coverage)[0]['status']=='missing'


def test_verify_gmail_document_satisfies_expectation(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    from goco.models.reporting.expectations import evaluate
    store=DocumentStore(tmp_path/'docs', Index(tmp_path/'state'/'index.sqlite3'))
    first=store.ingest(candidate())
    before=store.documents()[0]
    outcome=store.verify(first.document_id, dict(document_type='invoice',period_start='2026-10-01',period_end='2026-10-31',reviewed_by='Reviewer'), expectation=obligation())
    assert outcome.document_id==first.document_id
    doc=store.documents()[0]
    assert doc.sha256==before.sha256 and doc.history[0]['reviewed_by']=='Reviewer'
    assert evaluate([obligation()],[doc],date(2026,11,8),[])[0]['status']=='satisfied'
    store.index.rebuild(store.root)
    assert store.index.documents()[0].history==doc.history


def test_source_message_cannot_be_verified_as_invoice(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    store=DocumentStore(tmp_path/'docs', Index(tmp_path/'index.sqlite3'))
    source=replace(candidate(content=b'Subject: receipt\r\n\r\nMessage'), document_type='unknown', original_filename='original.eml')
    first=store.ingest(source)
    with pytest.raises(ValueError):
        store.verify(first.document_id, dict(document_type='invoice',period_start='2026-10-01',period_end='2026-10-31',reviewed_by='Reviewer'))
