from collections import Counter
from dataclasses import dataclass, field


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
    account_ids: list[str] = field(default_factory=list)

    @property
    def counts(self):
        return dict(Counter(item["outcome"] for item in self.outcomes))

    @property
    def account_results(self):
        """Per selected account: what was stored, found again, left for review, and whether the run failed."""
        results = []
        for account_id in self.account_ids:
            items = [item for item in self.outcomes if item["account_id"] == account_id]
            failure = next((item for item in items if item["outcome"] in ("failed", "auth_required")), None)
            stored = [item for item in items if item["document_id"] and item["outcome"] in ("saved", "review_required")]
            if failure:
                status, reason = failure["outcome"], failure["reason"]
            elif items:
                status, reason = "completed", None
            else:
                status, reason = "no_matches", None
            results.append(dict(
                account_id=account_id, status=status, reason=reason,
                saved=len(stored),
                metadata_review=sum(item["outcome"] == "review_required" for item in stored),
                duplicate=sum(item["outcome"] == "duplicate" for item in items),
                review=sum(item["outcome"] == "review_required" and not item["document_id"] for item in items),
                skipped=sum(item["outcome"] == "skipped" for item in items),
                manual=sum(item["outcome"] == "manual_required" for item in items),
            ))
        return results
