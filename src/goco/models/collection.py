from collections import Counter
from dataclasses import dataclass


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
        return dict(Counter(item["outcome"] for item in self.outcomes))
