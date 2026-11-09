from __future__ import annotations

import socket
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from report_publisher.models import TimeEntry

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def sample_csv() -> Path:
    return EXAMPLES / "time_entries.csv"


def entry(
    day: str = "2026-11-02",
    client: str = "Contoso",
    category: str = "Break/Fix",
    hours: str = "1",
    billable: bool = True,
    project: str = "Support",
    staff: str = "Jordan Avery",
    description: str = "work",
) -> TimeEntry:
    return TimeEntry(
        day=date.fromisoformat(day),
        staff=staff,
        client=client,
        project=project,
        category=category,
        hours=Decimal(hours),
        billable=billable,
        description=description,
    )


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a real tenant. `responses` mocks below the socket layer."""

    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("real network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", guard)
