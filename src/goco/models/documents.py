from dataclasses import dataclass, field

from .time import utcnow


DOCUMENT_TYPES = {"invoice", "receipt", "credit_note", "statement", "unknown"}
MAX_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class SourceRef:
    method: str
    origin_id: str
    retrieved_at: str = field(default_factory=utcnow)
    mailbox_id: str | None = None
    message_id: str | None = None
    attachment_id: str | None = None


@dataclass(frozen=True)
class InvoiceMetadata:
    issue_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    invoice_id: str | None = None
    amount: str | None = None
    currency: str | None = None
    provenance: str = "unknown"
    verified: bool = False


@dataclass(frozen=True)
class DocumentCandidate:
    account_id: str
    vendor_id: str
    document_type: str
    original_filename: str
    content: bytes
    source: SourceRef
    metadata: InvoiceMetadata = field(default_factory=InvoiceMetadata)
    rule_id: str | None = None
    expectation_id: str | None = None
    owner_id: str | None = None


@dataclass
class StoredDocument:
    document_id: str
    account_id: str
    vendor_id: str
    document_type: str
    sha256: str
    byte_count: int
    original_path: str
    original_filename: str
    metadata: dict
    sources: list[dict]
    owner_id: str | None = None
    expectation_ids: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    conflict: bool = False
    schema_version: int = 1
