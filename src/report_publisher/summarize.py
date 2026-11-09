"""Classify entries into effort buckets and roll them up for one ISO week."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from .models import TimeEntry


class Bucket(StrEnum):
    CLIENT = "Client Work"
    INTERNAL = "Internal Operations"
    PROFDEV = "Professional Development"


DEFAULT_INTERNAL_CLIENTS = ("Internal",)
DEFAULT_PROFDEV_CATEGORIES = ("Training", "Certification", "R&D")


@dataclass(frozen=True)
class BucketRules:
    """Work for your own company is Internal Operations, unless it's learning.

    Everything billed against any other client is Client Work. Matching ignores case.
    """

    internal_clients: frozenset[str] = frozenset(c.casefold() for c in DEFAULT_INTERNAL_CLIENTS)
    profdev_categories: frozenset[str] = frozenset(c.casefold() for c in DEFAULT_PROFDEV_CATEGORIES)

    @classmethod
    def from_names(
        cls, internal_clients: Iterable[str], profdev_categories: Iterable[str]
    ) -> BucketRules:
        return cls(
            internal_clients=frozenset(c.strip().casefold() for c in internal_clients),
            profdev_categories=frozenset(c.strip().casefold() for c in profdev_categories),
        )

    def classify(self, entry: TimeEntry) -> Bucket:
        if entry.client.casefold() not in self.internal_clients:
            return Bucket.CLIENT
        if entry.category.casefold() in self.profdev_categories:
            return Bucket.PROFDEV
        return Bucket.INTERNAL


_ISO_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")


def parse_iso_week(value: str) -> tuple[date, date]:
    """``"2026-W45"`` -> (Monday, Sunday) of that ISO week."""
    match = _ISO_WEEK.match(value.strip())
    if not match:
        raise ValueError(f"week {value!r} must look like 2026-W45")
    year, week = int(match[1]), int(match[2])
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError:
        raise ValueError(f"{value!r} is not a real ISO week") from None
    return monday, monday + timedelta(days=6)


def last_full_week(today: date) -> str:
    year, week, _ = (today - timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


@dataclass
class WeeklySummary:
    week: str
    start: date
    end: date
    staff: str | None
    entries: dict[Bucket, list[TimeEntry]] = field(default_factory=dict)

    def _all(self) -> list[TimeEntry]:
        return [e for bucket in self.entries.values() for e in bucket]

    @property
    def total_hours(self) -> Decimal:
        return sum((e.hours for e in self._all()), Decimal(0))

    @property
    def billable_hours(self) -> Decimal:
        return sum((e.hours for e in self._all() if e.billable), Decimal(0))

    def bucket_hours(self, bucket: Bucket) -> Decimal:
        return sum((e.hours for e in self.entries.get(bucket, [])), Decimal(0))

    def bucket_percent(self, bucket: Bucket) -> int:
        total = self.total_hours
        if total == 0:
            return 0
        return round(self.bucket_hours(bucket) / total * 100)

    def grouped(self, bucket: Bucket) -> dict[str, list[TimeEntry]]:
        """Client Work groups by client; the other buckets group by project."""
        groups: dict[str, list[TimeEntry]] = defaultdict(list)
        for e in self.entries.get(bucket, []):
            key = e.client if bucket is Bucket.CLIENT else (e.project or "General")
            groups[key].append(e)
        return dict(sorted(groups.items(), key=lambda kv: kv[0].casefold()))

    def client_hours(self) -> list[tuple[str, Decimal, Decimal]]:
        """(client, total, billable) for Client Work, largest first."""
        rows = [
            (
                client,
                sum((e.hours for e in es), Decimal(0)),
                sum((e.hours for e in es if e.billable), Decimal(0)),
            )
            for client, es in self.grouped(Bucket.CLIENT).items()
        ]
        return sorted(rows, key=lambda r: (-r[1], r[0].casefold()))


def summarize(
    entries: Iterable[TimeEntry],
    week: str,
    rules: BucketRules | None = None,
    staff: str | None = None,
) -> WeeklySummary:
    rules = rules or BucketRules()
    start, end = parse_iso_week(week)
    summary = WeeklySummary(week=week, start=start, end=end, staff=staff)
    for bucket in Bucket:
        summary.entries[bucket] = []

    wanted_staff = staff.casefold() if staff else None
    for e in sorted(entries, key=lambda x: (x.day, x.client.casefold())):
        if not start <= e.day <= end:
            continue
        if wanted_staff and e.staff.casefold() != wanted_staff:
            continue
        summary.entries[rules.classify(e)].append(e)
    return summary
