from dataclasses import replace
from pathlib import Path
import hashlib
import pytest
from goco.models import DocumentCandidate, SourceRef, InvoiceMetadata


def candidate(account='a', content=b'%PDF-1.7 original', invoice_id=None, source='one'):
    return DocumentCandidate(account, 'vendor', 'invoice', '../unsafe.pdf', content,
        SourceRef('manual', source), InvoiceMetadata(invoice_id=invoice_id))


def test_same_bytes_same_account_duplicate_other_account_separate(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    store = DocumentStore(tmp_path/'docs', Index(tmp_path/'state'/'index.sqlite3'))
    first = store.ingest(candidate())
    again = store.ingest(candidate(source='two'))
    other = store.ingest(candidate('b'))
    assert first.document_id == again.document_id
    assert again.outcome == 'duplicate'
    assert other.document_id != first.document_id
    docs = store.documents()
    doc = next(d for d in docs if d.document_id == first.document_id)
    assert len(doc.sources) == 2
    assert (store.root/doc.original_path).read_bytes() == b'%PDF-1.7 original'
    assert doc.sha256 == hashlib.sha256(b'%PDF-1.7 original').hexdigest()
    assert (store.root/doc.original_path).is_relative_to(store.root)


def test_changed_invoice_id_content_preserves_versions(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    store = DocumentStore(tmp_path/'docs', Index(tmp_path/'index.sqlite3'))
    a = store.ingest(candidate(invoice_id='INV-1'))
    b = store.ingest(candidate(content=b'%PDF-1.7 corrected', invoice_id='INV-1'))
    assert a.document_id != b.document_id
    assert b.outcome == 'review_required'
    assert len(store.documents()) == 2


@pytest.mark.parametrize('content,extension', [(b'not pdf','.pdf'),(b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///secret">]><x>&x;</x>','.xml'),(b'', '.pdf')])
def test_rejects_invalid_original(tmp_path, content, extension):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    store = DocumentStore(tmp_path/'docs', Index(tmp_path/'index.sqlite3'))
    with pytest.raises(ValueError):
        store.ingest(replace(candidate(content=content), original_filename='source'+extension))
    assert not store.documents()
