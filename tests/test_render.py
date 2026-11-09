from __future__ import annotations

from datetime import date

from report_publisher.render import render_markdown
from report_publisher.summarize import summarize

from .conftest import EXAMPLES, entry


def test_example_report_is_up_to_date(sample_csv, tmp_path):
    """examples/weekly_report_2026-W45.md is what the README shows; keep it honest."""
    from report_publisher.cli import main

    rc = main(
        [str(sample_csv), "--week", "2026-W45", "--staff", "Jordan Avery", "--out", str(tmp_path)],
        today=lambda: date(2026, 11, 9),
        env={},
    )
    assert rc == 0
    generated = (tmp_path / "weekly_report_2026-W45.md").read_text(encoding="utf-8")
    expected = (EXAMPLES / "weekly_report_2026-W45.md").read_text(encoding="utf-8")
    assert generated == expected


def test_escapes_pipes_and_newlines_in_cells():
    s = summarize([entry(description="a | b\nc")], "2026-W45")
    md = render_markdown(s, generated=date(2026, 11, 9))
    assert "a \\| b c" in md


def test_empty_week_renders():
    md = render_markdown(summarize([], "2026-W45"), generated=date(2026, 11, 9))
    assert md.count("_No entries this week._") == 3
    assert "| **Total** | **0h** | 0% |" in md
    assert "Hours by client" not in md


def test_hours_formatting():
    s = summarize([entry(hours="2.50"), entry(hours="3.00", client="B")], "2026-W45")
    md = render_markdown(s, generated=date(2026, 11, 9))
    assert "**Total:** 5.5h" in md
    assert "| 2.5h |" in md
    assert "| 3h |" in md
