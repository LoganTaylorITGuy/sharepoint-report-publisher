"""Render a WeeklySummary as Markdown (readable on GitHub, SharePoint, or an LLM notebook)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import TimeEntry
from .summarize import Bucket, WeeklySummary


def _h(hours: Decimal) -> str:
    """2.50 -> '2.5h', 3.00 -> '3h'."""
    text = f"{hours.normalize():f}"
    return f"{text}h"


def _cell(text: str) -> str:
    """Make arbitrary text safe inside a Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def _entry_row(e: TimeEntry, *, with_project: bool) -> list[str]:
    row = [e.day.isoformat()]
    if with_project:
        row.append(e.project or "—")
    row += [e.category or "—", _h(e.hours), "B" if e.billable else "NB", e.description or "—"]
    return row


def render_markdown(summary: WeeklySummary, generated: date) -> str:
    s = summary
    out: list[str] = [
        "# Weekly Effort Report",
        "",
        f"**Staff:** {s.staff or 'All staff'}  ",
        f"**Period:** {s.week} ({s.start.isoformat()} to {s.end.isoformat()})  ",
        f"**Generated:** {generated.isoformat()}  ",
        f"**Total:** {_h(s.total_hours)} | Billable: {_h(s.billable_hours)} | "
        f"Non-billable: {_h(s.total_hours - s.billable_hours)}",
        "",
    ]

    for bucket in Bucket:
        out += [f"## {bucket.value}", ""]
        groups = s.grouped(bucket)
        if not groups:
            out += ["_No entries this week._", ""]
            continue
        for name, entries in groups.items():
            out += [f"### {name}", ""]
            is_client = bucket is Bucket.CLIENT
            header = ["Date", "Project", "Category", "Hours", "B/NB", "Description"]
            if not is_client:
                header.remove("Project")
            out += _table(header, [_entry_row(e, with_project=is_client) for e in entries])
            subtotal = sum((e.hours for e in entries), Decimal(0))
            billable = sum((e.hours for e in entries if e.billable), Decimal(0))
            out += ["", f"**Subtotal:** {_h(subtotal)} ({_h(billable)} billable)", ""]

    out += ["## Summary", ""]
    out += _table(
        ["Bucket", "Hours", "% of week"],
        [[b.value, _h(s.bucket_hours(b)), f"{s.bucket_percent(b)}%"] for b in Bucket]
        + [["**Total**", f"**{_h(s.total_hours)}**", "100%" if s.total_hours else "0%"]],
    )

    clients = s.client_hours()
    if clients:
        out += ["", "### Hours by client", ""]
        out += _table(
            ["Client", "Hours", "Billable"],
            [[name, _h(total), _h(billable)] for name, total, billable in clients],
        )

    out.append("")
    return "\n".join(out)
