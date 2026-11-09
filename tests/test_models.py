from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest

from report_publisher.models import InputError, read_entries

HEADER = "Date,Staff,Client,Project,Category,Hours,Billable,Description\n"


def parse(body: str, header: str = HEADER):
    return read_entries(io.StringIO(header + body))


def test_reads_sample_file(sample_csv):
    with sample_csv.open(newline="", encoding="utf-8") as f:
        entries = read_entries(f)
    assert len(entries) == 12
    assert entries[1].hours == Decimal("1.5")
    assert entries[1].billable is True


def test_parses_fields():
    [e] = parse("2026-11-02, Jordan ,Contoso, Support ,Break/Fix,1.25,no,Fixed it\n")
    assert e.day == date(2026, 11, 2)
    assert e.staff == "Jordan"
    assert e.project == "Support"
    assert e.hours == Decimal("1.25")
    assert e.billable is False


def test_accepts_us_dates():
    [e] = parse("11/02/2026,J,Contoso,P,C,1,yes,x\n")
    assert e.day == date(2026, 11, 2)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Yes", True), ("TRUE", True), ("1", True), ("Billable", True), ("No", False), ("", False)],
)
def test_billable_values(value, expected):
    [e] = parse(f"2026-11-02,J,Contoso,P,C,1,{value},x\n")
    assert e.billable is expected


def test_tolerates_header_whitespace_and_blank_lines():
    header = " Date , Staff,Client,Project,Category,Hours,Billable,Description\n"
    entries = parse("2026-11-02,J,Contoso,P,C,1,yes,x\n,,,,,,,\n\n", header=header)
    assert len(entries) == 1


def test_missing_columns_are_named():
    with pytest.raises(InputError, match=r"missing column.*Hours.*Billable"):
        read_entries(io.StringIO("Date,Staff,Client,Project,Category,Description\n"))


def test_reports_every_bad_row_at_once():
    body = (
        "2026-11-02,J,Contoso,P,C,1,yes,ok\n"
        "2026-13-40,J,Contoso,P,C,1,yes,bad date\n"
        "2026-11-02,J,Contoso,P,C,abc,yes,bad hours\n"
        "2026-11-02,J,Contoso,P,C,25,yes,too many hours\n"
        "2026-11-02,J,Contoso,P,C,0,yes,zero hours\n"
        "2026-11-02,J,Contoso,P,C,1,maybe,bad billable\n"
        "2026-11-02,J,,P,C,1,yes,no client\n"
    )
    with pytest.raises(InputError) as info:
        parse(body)
    problems = info.value.problems
    assert [p.split(":")[0] for p in problems] == [
        "line 3",
        "line 4",
        "line 5",
        "line 6",
        "line 7",
        "line 8",
    ]
    assert "unrecognised date" in problems[0]
    assert "not a number" in problems[1]
    assert "at most 24" in problems[2]
    assert "not yes/no" in problems[4]
    assert "client is empty" in problems[5]
