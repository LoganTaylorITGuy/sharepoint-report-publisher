from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from report_publisher.cli import EXIT_GRAPH, EXIT_INPUT, EXIT_OK, main
from report_publisher.graph import AppCredentials, GraphError

ENV = {"GRAPH_TENANT_ID": "t", "GRAPH_CLIENT_ID": "c", "GRAPH_CLIENT_SECRET": "s"}


class FakeClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail_on = fail_on

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise GraphError(f"{name} exploded")

    def resolve_site_id(self, site: str) -> str:
        self.calls.append(("site", site))
        self._maybe_fail("site")
        return "site-1"

    def resolve_drive_id(self, site_id: str, library: str | None) -> str:
        self.calls.append(("drive", (site_id, library)))
        return "drive-1"

    def upload_file(self, drive_id: str, folder: str, filename: str, content: bytes) -> dict:
        self.calls.append(("upload", (drive_id, folder, filename, len(content))))
        self._maybe_fail("upload")
        return {"webUrl": f"https://contoso.sharepoint.com/{folder}/{filename}"}


def run(args, **kwargs):
    kwargs.setdefault("today", lambda: date(2026, 11, 9))
    kwargs.setdefault("env", {})
    return main(args, **kwargs)


def test_writes_report_for_last_full_week_by_default(sample_csv, tmp_path, capsys):
    assert run([str(sample_csv), "--out", str(tmp_path)]) == EXIT_OK
    report = tmp_path / "weekly_report_2026-W45.md"
    assert report.exists()
    assert "**Staff:** All staff" in report.read_text(encoding="utf-8")
    assert "wrote" in capsys.readouterr().out


def test_custom_bucket_rules(tmp_path):
    csv = tmp_path / "e.csv"
    csv.write_text(
        "Date,Staff,Client,Project,Category,Hours,Billable,Description\n"
        "2026-11-02,J,Acme IT,Lab,Homelab,2,no,lab\n",
        encoding="utf-8",
    )
    rc = run(
        [
            str(csv),
            "--week",
            "2026-W45",
            "--internal-client",
            "Acme IT",
            "--profdev-category",
            "Homelab",
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == EXIT_OK
    text = (tmp_path / "weekly_report_2026-W45.md").read_text(encoding="utf-8")
    assert "| Professional Development | 2h | 100% |" in text


def test_reports_all_input_problems(tmp_path, capsys):
    csv = tmp_path / "bad.csv"
    csv.write_text(
        "Date,Staff,Client,Project,Category,Hours,Billable,Description\n"
        "nope,J,C,P,C,1,yes,x\n2026-11-02,J,C,P,C,99,yes,x\n",
        encoding="utf-8",
    )
    assert run([str(csv)]) == EXIT_INPUT
    err = capsys.readouterr().err
    assert "2 problem(s)" in err
    assert "line 2" in err
    assert "line 3" in err


def test_missing_file_and_bad_week(sample_csv, tmp_path, capsys):
    assert run([str(tmp_path / "nope.csv")]) == EXIT_INPUT
    assert run([str(sample_csv), "--week", "2026-45"]) == EXIT_INPUT
    assert "must look like 2026-W45" in capsys.readouterr().err


def test_publishes_to_sharepoint(sample_csv, tmp_path, capsys):
    fake = FakeClient()
    seen: list[AppCredentials] = []

    def factory(creds):
        seen.append(creds)
        return fake

    rc = run(
        [
            str(sample_csv),
            "--week",
            "2026-W45",
            "--out",
            str(tmp_path),
            "--site",
            "contoso.sharepoint.com:/sites/Ops",
            "--library",
            "Reports",
        ],
        env=ENV,
        client_factory=factory,
    )
    assert rc == EXIT_OK
    assert seen[0].client_secret == "s"
    assert fake.calls[0] == ("site", "contoso.sharepoint.com:/sites/Ops")
    assert fake.calls[1] == ("drive", ("site-1", "Reports"))
    assert fake.calls[2][1][:3] == ("drive-1", "Reports/Weekly", "weekly_report_2026-W45.md")
    assert "published https://contoso.sharepoint.com/" in capsys.readouterr().out


@pytest.mark.parametrize("fail_on", ["site", "upload"])
def test_publish_failure_keeps_local_report(sample_csv, tmp_path, capsys, fail_on):
    rc = run(
        [
            str(sample_csv),
            "--week",
            "2026-W45",
            "--out",
            str(tmp_path),
            "--site",
            "contoso.sharepoint.com:/sites/Ops",
        ],
        env=ENV,
        client_factory=lambda creds: FakeClient(fail_on=fail_on),
    )
    assert rc == EXIT_GRAPH
    assert (tmp_path / "weekly_report_2026-W45.md").exists()
    assert f"{fail_on} exploded" in capsys.readouterr().err


def test_publish_without_credentials(sample_csv, tmp_path, capsys):
    rc = run(
        [
            str(sample_csv),
            "--week",
            "2026-W45",
            "--out",
            str(tmp_path),
            "--site",
            "contoso.sharepoint.com:/sites/Ops",
        ],
    )
    assert rc == EXIT_GRAPH
    assert "missing configuration" in capsys.readouterr().err
