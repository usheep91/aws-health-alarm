from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aha.config import Config
from aha.domain.models import DueDateSource, EventSnapshot


def to_local_date(ts: datetime, tz: str) -> date:
    return ts.astimezone(ZoneInfo(tz)).date()


def _is_lifecycle(snap: EventSnapshot) -> bool:
    return snap.category == "scheduledChange" or "PLANNED_LIFECYCLE_EVENT" in snap.event_type_code


def sla_days(snap: EventSnapshot, cfg: Config) -> int:
    candidates = [cfg.sla_days["default"]]
    if snap.category == "issue":
        candidates.append(cfg.sla_days["issue"])
    if "SECURITY" in snap.personas:
        candidates.append(cfg.sla_days["SECURITY"])
    return min(candidates)


def compute_due(snap: EventSnapshot, first_seen: date, cfg: Config) -> tuple[date, DueDateSource]:
    if _is_lifecycle(snap):
        ts = snap.start_time or snap.end_time
        if ts is not None:
            return to_local_date(ts, cfg.timezone), DueDateSource.AWS
    elif snap.start_time is not None:
        start = to_local_date(snap.start_time, cfg.timezone)
        if start > first_seen:
            return start, DueDateSource.AWS
    return first_seen + timedelta(days=sla_days(snap, cfg)), DueDateSource.DEFAULT_SLA


def priority(personas: tuple[str, ...], due: date, today: date) -> str:
    days = (due - today).days
    if "SECURITY" in personas or days <= 14:
        return "P1"
    if days <= 30:
        return "P2"
    return "P3"


def d_label(due: date, today: date) -> str:
    days = (due - today).days
    if days == 0:
        return "D-day"
    return f"D-{days}" if days > 0 else f"D+{-days}"
