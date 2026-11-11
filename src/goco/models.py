from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any
import re

DOCUMENT_TYPES = {"invoice", "receipt", "credit_note", "statement", "unknown"}
MAX_BYTES = 25 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MonthWindow:
    month: str
    start_epoch: int
    end_epoch: int


def parse_month(value: str, tz: str = "Europe/Berlin") -> MonthWindow:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError("month: expected YYYY-MM")
    year, month = map(int, value.split("-"))
    start = datetime(year, month, 1, tzinfo=ZoneInfo(tz))
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=ZoneInfo(tz))
    return MonthWindow(value, int(start.timestamp()), int(end.timestamp()))


@dataclass(frozen=True)
class AppConfig:
    path: Path
    storage_root: Path
    state_dir: Path
    owners: tuple[dict, ...]
    mailboxes: tuple[dict, ...]
    accounts: tuple[dict, ...]
    rules: tuple[dict, ...]
    expectations: tuple[dict, ...]
    timezone: str = "Europe/Berlin"
    max_bytes: int = MAX_BYTES

    def account(self, account_id):
        for item in self.accounts:
            if item["id"] == account_id:
                return item
        raise ValueError("account: unknown ID")

    def mailbox(self, mailbox_id):
        for item in self.mailboxes:
            if item["id"] == mailbox_id:
                return item
        raise ValueError("mailbox: unknown ID")


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


@dataclass
class CollectionOutcome:
    account_id: str
    outcome: str
    reason: str
    document_id: str | None = None
    review_id: str | None = None


@dataclass(frozen=True)
class CollectRequest:
    month: str
    account_ids: tuple[str, ...]
    dry_run: bool = False


@dataclass
class RunSummary:
    run_id: str
    started_at: str
    completed_at: str
    outcomes: list[dict]
    coverage: list[dict]
    interrupted: bool = False
    schema_version: int = 1

    @property
    def counts(self):
        from collections import Counter
        return dict(Counter(item["outcome"] for item in self.outcomes))
