"""마일스톤 리마인더 판정 (spec 9.5 중 구현 범위). 순수 함수이며 저장/게시는 호출자가 한다.

reminders_sent 키: "D-90"…"D-1", "D-0"(D-day), "OVERDUE-1"(D+1 1회),
"OVERDUE-{ISO연도}-W{주차}"(이후 매주 월요일), BASELINE(첫 평가 완료 표시).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from aha.config import Config
from aha.domain.models import State, TrackingItem

BASELINE = "BASELINE"
OVERDUE_FIRST = "OVERDUE-1"
MONDAY = 0
WEEK_KEY = re.compile(r"^OVERDUE-\d{4}-W\d{2}$")


@dataclass(frozen=True)
class ReminderDecision:
    label: str | None
    sent: frozenset[str]
    mention: bool


def _milestone_key(m: int) -> str:
    return f"D-{m}"


def _week_key(today: date) -> str:
    iso = today.isocalendar()
    return f"OVERDUE-{iso.year}-W{iso.week:02d}"


def _with_week(sent: set[str], week: str) -> set[str]:
    """이번 주차 키만 남겨 reminders_sent가 매주 커지지 않게 한다(OVERDUE-1은 유지)."""
    return {k for k in sent if not WEEK_KEY.match(k)} | {week}


def decide_reminder(item: TrackingItem, today: date, cfg: Config) -> ReminderDecision:
    sent = set(item.reminders_sent)
    if item.state is not State.OPEN:
        return ReminderDecision(None, frozenset(sent), False)

    days_left = (item.due_date - today).days
    reached = sorted(m for m in cfg.reminder_milestones if days_left <= m)
    week = _week_key(today)
    label: str | None = None

    if BASELINE not in sent:
        # 이미 지난 마일스톤은 보낸 것으로 기록해, 기존·늦게 감지된 항목이 한꺼번에 울리지 않게 한다.
        sent |= {_milestone_key(m) for m in reached}
        if days_left < 0:
            sent = _with_week(sent, week) | {OVERDUE_FIRST}
        sent.add(BASELINE)
    elif days_left >= 0:
        newly = [m for m in reached if _milestone_key(m) not in sent]
        if newly:
            label = "D-day" if newly[0] == 0 else f"D-{newly[0]}"
            sent |= {_milestone_key(m) for m in reached}
    elif OVERDUE_FIRST not in sent:
        label = f"D+{-days_left}"
        sent = _with_week(sent, week) | {OVERDUE_FIRST}
    elif today.weekday() == MONDAY and week not in sent:
        label = f"D+{-days_left}"
        sent = _with_week(sent, week)

    mention = label is not None and days_left <= cfg.mention_within_days
    return ReminderDecision(label, frozenset(sent), mention)
