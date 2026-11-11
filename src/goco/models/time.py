import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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
