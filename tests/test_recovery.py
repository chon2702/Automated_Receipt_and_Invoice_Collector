import json
import pytest
from test_storage import candidate


def test_index_failure_after_publish_is_recoverable(tmp_path, monkeypatch):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    index = Index(tmp_path/'state'/'index.sqlite3')
    store = DocumentStore(tmp_path/'docs', index)
    original = index.upsert
    def fail(doc): raise OSError('disk full')
    monkeypatch.setattr(index, 'upsert', fail)
    with pytest.raises(OSError): store.ingest(candidate())
    assert len(list(store.root.rglob('original.pdf'))) == 1
    monkeypatch.setattr(index, 'upsert', original)
    assert index.rebuild(store.root)['documents'] == 1
    assert len(index.documents()) == 1
    assert store.ingest(candidate()).outcome == 'duplicate'


def test_corrupt_rebuild_preserves_previous_index(tmp_path):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    index = Index(tmp_path/'state'/'index.sqlite3')
    store = DocumentStore(tmp_path/'docs', index)
    first = store.ingest(candidate())
    doc = index.documents()[0]
    (store.root/doc.original_path).write_bytes(b'corrupt')
    with pytest.raises(ValueError): index.rebuild(store.root)
    assert index.documents()[0].document_id == first.document_id


def test_interrupted_publication_never_counts_staging(tmp_path, monkeypatch):
    from goco.models.persistence.storage import DocumentStore
    from goco.models.persistence.index import Index
    import goco.models.persistence.storage as storage
    store=DocumentStore(tmp_path/'docs', Index(tmp_path/'state'/'index.sqlite3'))
    def stop(stage, final): raise OSError('interrupted rename')
    monkeypatch.setattr(storage, 'publish_directory', stop)
    with pytest.raises(OSError): store.ingest(candidate())
    assert store.documents() == []
    assert store.index.rebuild(store.root)['documents'] == 0


def test_writer_lock_excludes_second_writer_and_releases(tmp_path):
    from goco.models.persistence.locking import WriterLock
    with WriterLock(tmp_path):
        with pytest.raises(OSError):
            with WriterLock(tmp_path): pass
    with WriterLock(tmp_path): pass
