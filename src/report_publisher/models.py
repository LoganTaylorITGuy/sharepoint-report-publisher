"""Parse and validate time entries from a CSV export."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TextIO

REQUIRED_COLUMNS = (
    "Date",
    "Staff",
    "Client",
    "Project",
    "Category",
    "Hours",
    "Billable",
    "Description",
)

_TRUE = {"yes", "y", "true", "1", "billable"}
_FALSE = {"no", "n", "false", "0", "non-billable", "nonbillable", ""}
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")
_MAX_HOURS_PER_ENTRY = Decimal(24)


class InputError(ValueError):
    """The CSV could not be read. ``problems`` lists every bad row, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True, slots=True)
class TimeEntry:
    day: date
    staff: str
    client: str
    project: str
    category: str
    hours: Decimal
    billable: bool
    description: str


def _parse_date(value: str) -> date:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r} (use YYYY-MM-DD or MM/DD/YYYY)")


def _parse_hours(value: str) -> Decimal:
    try:
        hours = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError(f"hours {value!r} is not a number") from None
    if not hours.is_finite() or hours <= 0 or hours > _MAX_HOURS_PER_ENTRY:
        raise ValueError(f"hours {value!r} must be greater than 0 and at most 24")
    return hours


def _parse_billable(value: str) -> bool:
    normalised = value.strip().lower()
    if normalised in _TRUE:
        return True
    if normalised in _FALSE:
        return False
    raise ValueError(f"billable {value!r} is not yes/no")


def read_entries(stream: TextIO) -> list[TimeEntry]:
    """Read every row, collecting all validation problems before failing.

    Reporting every bad row at once matters for exports: fixing one line at a time and
    re-running is how people end up hand-editing CSVs at 17:00 on a Friday.
    """
    reader = csv.DictReader(stream)
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise InputError([f"missing column(s): {', '.join(missing)}"])
    reader.fieldnames = header

    entries: list[TimeEntry] = []
    problems: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # blank line
        try:
            client = (row["Client"] or "").strip()
            if not client:
                raise ValueError("client is empty")
            entries.append(
                TimeEntry(
                    day=_parse_date(row["Date"] or ""),
                    staff=(row["Staff"] or "").strip(),
                    client=client,
                    project=(row["Project"] or "").strip(),
                    category=(row["Category"] or "").strip(),
                    hours=_parse_hours(row["Hours"] or ""),
                    billable=_parse_billable(row["Billable"] or ""),
                    description=(row["Description"] or "").strip(),
                )
            )
        except ValueError as exc:
            problems.append(f"line {line_no}: {exc}")

    if problems:
        raise InputError(problems)
    return entries
