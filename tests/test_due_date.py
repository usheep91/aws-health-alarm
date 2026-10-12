from datetime import UTC, date, datetime

import pytest

from aha.domain.due_date import compute_due, d_label, priority, to_local_date
from aha.domain.models import DueDateSource
from tests.factories import TODAY, make_config, make_snapshot

CFG = make_config()


def test_to_local_date_uses_kst():
    assert to_local_date(datetime(2026, 11, 30, 16, 0, tzinfo=UTC), "Asia/Seoul") == date(2026, 12, 1)


def test_lifecycle_uses_start_time():
    assert compute_due(make_snapshot(), TODAY, CFG) == (date(2026, 11, 30), DueDateSource.AWS)


def test_lifecycle_falls_back_to_end_time():
    snap = make_snapshot(start_time=None, end_time=datetime(2027, 1, 15, tzinfo=UTC))
    assert compute_due(snap, TODAY, CFG) == (date(2027, 1, 15), DueDateSource.AWS)


def test_lifecycle_without_dates_uses_default_sla():
    snap = make_snapshot(start_time=None, end_time=None)
    assert compute_due(snap, TODAY, CFG) == (date(2026, 11, 11), DueDateSource.DEFAULT_SLA)


def test_notification_with_future_start_uses_start():
    snap = make_snapshot(category="accountNotification", event_type_code="AWS_IAM_NOTICE")
    assert compute_due(snap, TODAY, CFG) == (date(2026, 11, 30), DueDateSource.AWS)


def test_notification_with_past_start_uses_sla():
    snap = make_snapshot(
        category="accountNotification",
        event_type_code="AWS_IAM_NOTICE",
        start_time=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert compute_due(snap, TODAY, CFG) == (date(2026, 11, 11), DueDateSource.DEFAULT_SLA)


@pytest.mark.parametrize(
    ("category", "personas", "expected"),
    [
        ("issue", ("OPERATIONS",), date(2026, 10, 15)),
        ("accountNotification", ("SECURITY",), date(2026, 10, 19)),
        ("issue", ("SECURITY",), date(2026, 10, 15)),
    ],
)
def test_sla_takes_shortest_matching_rule(category, personas, expected):
    snap = make_snapshot(category=category, event_type_code="AWS_X_NOTICE", start_time=None, personas=personas)
    assert compute_due(snap, TODAY, CFG) == (expected, DueDateSource.DEFAULT_SLA)


@pytest.mark.parametrize(
    ("personas", "due", "expected"),
    [
        (("SECURITY",), date(2027, 6, 1), "P1"),
        (("OPERATIONS",), date(2026, 10, 26), "P1"),
        (("OPERATIONS",), date(2026, 11, 11), "P2"),
        (("OPERATIONS",), date(2026, 11, 12), "P3"),
    ],
)
def test_priority(personas, due, expected):
    assert priority(personas, due, TODAY) == expected


@pytest.mark.parametrize(
    ("due", "expected"),
    [(date(2026, 11, 30), "D-49"), (TODAY, "D-day"), (date(2026, 10, 9), "D+3")],
)
def test_d_label(due, expected):
    assert d_label(due, TODAY) == expected
