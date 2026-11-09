from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from report_publisher.summarize import (
    Bucket,
    BucketRules,
    last_full_week,
    parse_iso_week,
    summarize,
)

from .conftest import entry


def test_classification_defaults():
    rules = BucketRules()
    assert rules.classify(entry(client="Contoso")) is Bucket.CLIENT
    assert rules.classify(entry(client="internal", category="Documentation")) is Bucket.INTERNAL
    assert rules.classify(entry(client="Internal", category="training")) is Bucket.PROFDEV
    # Training billed to a client is still client work.
    assert rules.classify(entry(client="Contoso", category="Training")) is Bucket.CLIENT


def test_custom_rules():
    rules = BucketRules.from_names(["Acme IT", " Acme IT, LLC "], ["Courses"])
    assert rules.classify(entry(client="acme it, llc", category="Ops")) is Bucket.INTERNAL
    assert rules.classify(entry(client="Acme IT", category="courses")) is Bucket.PROFDEV
    assert rules.classify(entry(client="Internal")) is Bucket.CLIENT


def test_iso_week_bounds():
    assert parse_iso_week("2026-W45") == (date(2026, 11, 2), date(2026, 11, 8))
    # Week 1 of 2026 starts in December 2025.
    assert parse_iso_week("2026-W01") == (date(2025, 12, 29), date(2026, 1, 4))


@pytest.mark.parametrize("bad", ["2026-45", "W45", "2026-W60", "2025-W53"])
def test_iso_week_rejects_bad_input(bad):
    with pytest.raises(ValueError, match=r"week|ISO week"):
        parse_iso_week(bad)


def test_last_full_week():
    assert last_full_week(date(2026, 11, 9)) == "2026-W45"  # Monday -> previous week
    assert last_full_week(date(2026, 1, 2)) == "2025-W52"  # crosses a year boundary
    assert last_full_week(date(2027, 1, 6)) == "2026-W53"  # 2026 has 53 ISO weeks


def test_summary_filters_week_and_staff_and_totals():
    entries = [
        entry(day="2026-11-01", hours="9"),  # Sunday of the previous week
        entry(day="2026-11-02", hours="2", client="Contoso"),
        entry(day="2026-11-03", hours="1.5", client="Fabrikam", billable=False),
        entry(day="2026-11-04", hours="1", client="Internal", category="Training"),
        entry(day="2026-11-08", hours="0.5", client="Internal", category="Ops", billable=False),
        entry(day="2026-11-09", hours="9"),  # Monday of the next week
        entry(day="2026-11-04", hours="9", staff="Someone Else"),
    ]
    s = summarize(entries, "2026-W45", staff="jordan avery")
    assert s.total_hours == Decimal("5")
    assert s.billable_hours == Decimal("3")
    assert s.bucket_hours(Bucket.CLIENT) == Decimal("3.5")
    assert s.bucket_percent(Bucket.CLIENT) == 70
    assert s.bucket_percent(Bucket.PROFDEV) == 20
    assert s.bucket_percent(Bucket.INTERNAL) == 10
    assert s.client_hours() == [
        ("Contoso", Decimal("2"), Decimal("2")),
        ("Fabrikam", Decimal("1.5"), Decimal("0")),
    ]


def test_grouping_by_project_for_internal_work():
    s = summarize(
        [
            entry(client="Internal", project="", category="Ops"),
            entry(client="Internal", project="Automation", category="Ops"),
        ],
        "2026-W45",
    )
    assert list(s.grouped(Bucket.INTERNAL)) == ["Automation", "General"]


def test_empty_week():
    s = summarize([], "2026-W45")
    assert s.total_hours == 0
    assert s.bucket_percent(Bucket.CLIENT) == 0
    assert s.client_hours() == []
