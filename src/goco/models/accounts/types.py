from dataclasses import dataclass
from pathlib import Path

from ..documents import MAX_BYTES


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
