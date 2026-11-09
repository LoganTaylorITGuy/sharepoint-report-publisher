"""Command-line entry point: ``report-publisher``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from .graph import AppCredentials, GraphClient, GraphError, MsalTokenProvider
from .models import InputError, read_entries
from .render import render_markdown
from .summarize import (
    DEFAULT_INTERNAL_CLIENTS,
    DEFAULT_PROFDEV_CATEGORIES,
    BucketRules,
    last_full_week,
    summarize,
)

EXIT_OK, EXIT_INPUT, EXIT_GRAPH = 0, 2, 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="report-publisher",
        description="Build a weekly effort report from a time-tracking CSV export "
        "and optionally publish it to SharePoint.",
    )
    p.add_argument("csv", type=Path, help="time entry export (CSV)")
    p.add_argument("--week", help="ISO week, e.g. 2026-W45 (default: last full week)")
    p.add_argument("--staff", help="only include this person's entries")
    p.add_argument(
        "--out", type=Path, default=Path("."), help="output folder (default: current folder)"
    )
    p.add_argument(
        "--internal-client",
        action="append",
        metavar="NAME",
        help=f"client name that means 'our own company' (repeatable; "
        f"default: {', '.join(DEFAULT_INTERNAL_CLIENTS)})",
    )
    p.add_argument(
        "--profdev-category",
        action="append",
        metavar="NAME",
        help=f"internal category counted as Professional Development (repeatable; "
        f"default: {', '.join(DEFAULT_PROFDEV_CATEGORIES)})",
    )

    pub = p.add_argument_group("publish to SharePoint (needs GRAPH_* environment variables)")
    pub.add_argument("--site", help="e.g. contoso.sharepoint.com:/sites/Operations")
    pub.add_argument("--library", help="document library name (default: the site's default)")
    pub.add_argument("--folder", default="Reports/Weekly", help="folder in the library")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    today: Callable[[], date] = date.today,
    env: dict[str, str] | None = None,
    client_factory: Callable[[AppCredentials], GraphClient] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ) if env is None else env

    try:
        with args.csv.open(newline="", encoding="utf-8-sig") as f:
            entries = read_entries(f)
        week = args.week or last_full_week(today())
        rules = BucketRules.from_names(
            args.internal_client or DEFAULT_INTERNAL_CLIENTS,
            args.profdev_category or DEFAULT_PROFDEV_CATEGORIES,
        )
        summary = summarize(entries, week, rules=rules, staff=args.staff)
    except InputError as exc:
        print(f"error: {len(exc.problems)} problem(s) in {args.csv}:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_INPUT
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    report = render_markdown(summary, generated=today())
    filename = f"weekly_report_{week}.md"
    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / filename
    out_path.write_text(report, encoding="utf-8")
    print(
        f"wrote {out_path} ({summary.total_hours.normalize():f}h across "
        f"{sum(len(v) for v in summary.entries.values())} entries)"
    )

    if not args.site:
        return EXIT_OK

    try:
        creds = AppCredentials.from_env(env)
        client = (client_factory or _default_client)(creds)
        site_id = client.resolve_site_id(args.site)
        drive_id = client.resolve_drive_id(site_id, args.library)
        item = client.upload_file(drive_id, args.folder, filename, report.encode("utf-8"))
    except (GraphError, OSError) as exc:
        print(f"error: publish failed: {exc}", file=sys.stderr)
        return EXIT_GRAPH

    print(f"published {item.get('webUrl', filename)}")
    return EXIT_OK


def _default_client(creds: AppCredentials) -> GraphClient:
    return GraphClient(MsalTokenProvider(creds))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
