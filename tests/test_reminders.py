from datetime import date, timedelta

import pytest

from aha.domain.models import State
from aha.domain.reminders import BASELINE, ReminderDecision, decide_reminder
from tests.factories import make_config, make_item

CFG = make_config()
DUE = date(2026, 11, 30)  # 월요일, ISO 2026-W49
ALL_MILESTONES = {"D-90", "D-60", "D-30", "D-14", "D-7", "D-3", "D-1", "D-0"}


def _at(days_left: int) -> date:
    return DUE - timedelta(days=days_left)


def _decide(days_left=None, sent=(), today=None, due=DUE, **overrides):
    item = make_item(due_date=due, reminders_sent=set(sent), **overrides)
    return decide_reminder(item, today if today is not None else _at(days_left), CFG)


# --- 기준선(첫 평가) ---

@pytest.mark.parametrize(
    ("days_left", "expected"),
    [
        (120, {BASELINE}),
        (31, {BASELINE, "D-90", "D-60"}),
        (14, {BASELINE, "D-90", "D-60", "D-30", "D-14"}),
        (0, {BASELINE} | ALL_MILESTONES),
        (-3, {BASELINE, "OVERDUE-1", "OVERDUE-2026-W49"} | ALL_MILESTONES),
    ],
)
def test_baseline_marks_reached_milestones_without_sending(days_left, expected):
    decision = _decide(days_left)
    assert decision == ReminderDecision(label=None, sent=frozenset(expected), mention=False)


# --- 마일스톤 ---

def test_milestone_reached_the_day_after_baseline():
    baseline = _decide(31)
    decision = _decide(30, sent=baseline.sent)
    assert decision.label == "D-30"
    assert decision.sent == baseline.sent | {"D-30"}
    assert decision.mention is False


def test_same_day_rerun_sends_nothing():
    first = _decide(30, sent={BASELINE, "D-90", "D-60"})
    again = _decide(30, sent=first.sent)
    assert again.label is None
    assert again.sent == first.sent


def test_skipped_milestones_send_only_the_smallest():
    decision = _decide(5, sent={BASELINE, "D-90", "D-60"})
    assert decision.label == "D-7"
    assert decision.sent == {BASELINE, "D-90", "D-60", "D-30", "D-14", "D-7"}


def test_between_milestones_sends_nothing():
    decision = _decide(20, sent={BASELINE, "D-90", "D-60", "D-30"})
    assert decision.label is None


def test_d_day():
    decision = _decide(0, sent={BASELINE} | ALL_MILESTONES - {"D-0"})
    assert decision.label == "D-day"
    assert "D-0" in decision.sent
    assert decision.mention is True


@pytest.mark.parametrize(("days_left", "mention"), [(30, False), (14, True), (7, True)])
def test_mention_threshold(days_left, mention):
    reached = {f"D-{m}" for m in (90, 60, 30, 14, 7) if days_left < m}
    decision = _decide(days_left, sent={BASELINE} | reached)
    assert decision.label == f"D-{days_left}"
    assert decision.mention is mention


# --- 기한 경과 ---

def test_overdue_d_plus_one_sent_once():
    sent = {BASELINE} | ALL_MILESTONES
    first = _decide(-1, sent=sent)  # 2026-12-01 화요일
    assert first.label == "D+1"
    assert first.mention is True
    assert first.sent == sent | {"OVERDUE-1", "OVERDUE-2026-W49"}
    later = _decide(-2, sent=first.sent)
    assert later.label is None
    assert later.sent == first.sent


def test_monday_after_d_plus_one_sends_weekly():
    sent = {BASELINE, "OVERDUE-1", "OVERDUE-2026-W49"} | ALL_MILESTONES
    decision = _decide(-7, sent=sent)  # 2026-12-07 월요일, W50
    assert decision.label == "D+7"
    # 지난 주차 키는 지우고 OVERDUE-1과 이번 주차만 남긴다.
    assert decision.sent == (sent - {"OVERDUE-2026-W49"}) | {"OVERDUE-2026-W50"}
    assert _decide(-7, sent=decision.sent).label is None


def test_d_plus_one_on_monday_is_not_duplicated_that_week():
    sunday_due = date(2026, 12, 6)
    sent = {BASELINE} | ALL_MILESTONES
    first = _decide(sent=sent, today=date(2026, 12, 7), due=sunday_due)
    assert first.label == "D+1"
    assert first.sent == sent | {"OVERDUE-1", "OVERDUE-2026-W50"}
    assert _decide(sent=first.sent, today=date(2026, 12, 7), due=sunday_due).label is None
    assert _decide(sent=first.sent, today=date(2026, 12, 8), due=sunday_due).label is None
    weekly = _decide(sent=first.sent, today=date(2026, 12, 14), due=sunday_due)
    assert weekly.label == "D+8"
    assert "OVERDUE-2026-W51" in weekly.sent


def test_overdue_non_monday_without_new_week_sends_nothing():
    sent = {BASELINE, "OVERDUE-1", "OVERDUE-2026-W49"} | ALL_MILESTONES
    assert _decide(-9, sent=sent).label is None  # 2026-12-09 수요일


# --- 대상 외 ---

@pytest.mark.parametrize("state", [State.TRIAGE, State.RESOLVED_AUTO, State.RESOLVED_MANUAL, State.NOT_APPLICABLE, State.CLOSED_BY_AWS])
def test_non_open_items_get_no_reminder(state):
    decision = _decide(0, sent={"D-90"}, state=state)
    assert decision == ReminderDecision(label=None, sent=frozenset({"D-90"}), mention=False)


def test_custom_milestones_and_mention_window():
    cfg = make_config(reminder_milestones=[10, 0], mention_within_days=0)
    item = make_item(due_date=DUE, reminders_sent={BASELINE})
    decision = decide_reminder(item, _at(10), cfg)
    assert (decision.label, decision.mention) == ("D-10", False)
    assert decision.sent == {BASELINE, "D-10"}


def test_weekly_keys_do_not_accumulate():
    sent = {BASELINE, "OVERDUE-1", "OVERDUE-2026-W49", "OVERDUE-2026-W50"} | ALL_MILESTONES
    decision = _decide(sent=sent, today=date(2026, 12, 14))  # W51 월요일
    assert decision.label == "D+14"
    assert {k for k in decision.sent if k.startswith("OVERDUE-")} == {"OVERDUE-1", "OVERDUE-2026-W51"}
