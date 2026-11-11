from dataclasses import dataclass, field
from pathlib import Path
import hashlib
from goco.models import DocumentCandidate, SourceRef, InvoiceMetadata
from goco.models.documents.validation import metadata_valid
from .validation import expectation_matches


@dataclass(frozen=True)
class ImportMetadata:
    account: dict
    document_type: str
    metadata: InvoiceMetadata = field(default_factory=InvoiceMetadata)
    expectation: dict | None = None


def import_file(path: Path, candidate_metadata: ImportMetadata, store):
    path=Path(path)
    if path.stat().st_size>store.maximum: raise ValueError('invalid_document_size')
    with path.open('rb') as stream: content=stream.read(store.maximum+1)
    meta=candidate_metadata
    metadata_valid(vars(meta.metadata))
    if meta.expectation and not expectation_matches(meta.expectation,meta.account['id'],meta.document_type,vars(meta.metadata)):
        raise ValueError('expectation_metadata_mismatch')
    return store.ingest(DocumentCandidate(meta.account['id'],meta.account['vendor_id'],meta.document_type,path.name,content,
        SourceRef('manual',hashlib.sha256(content).hexdigest()),meta.metadata,expectation_id=meta.expectation['id'] if meta.expectation else None,owner_id=meta.account['owner_id']))
