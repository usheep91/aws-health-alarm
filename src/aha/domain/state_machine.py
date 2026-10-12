from __future__ import annotations

from datetime import datetime, timedelta

from aha.domain.models import EntityCounts, State, Tier, TrackingItem

# AWS 리소스 상태 반영은 최대 72시간 지연될 수 있다.
MISMATCH_GRACE = timedelta(hours=72)
ACTIVE_STATES = (State.TRIAGE, State.OPEN)


def next_state(item: TrackingItem, counts: EntityCounts, dynamic: bool, status_code: str) -> State:
    if item.state in ACTIVE_STATES:
        if dynamic and counts.total > 0 and counts.resolved == counts.total:
            return State.RESOLVED_AUTO
        if status_code == "closed" and not dynamic:
            return State.CLOSED_BY_AWS
        return item.state
    if item.state is State.RESOLVED_AUTO and counts.pending > 0:
        return State.OPEN if item.tier is Tier.REQUIRED else State.TRIAGE
    return item.state


def needs_mismatch_flag(item: TrackingItem, counts: EntityCounts, now: datetime) -> bool:
    return (
        item.state is State.RESOLVED_MANUAL
        and not item.verification_mismatch
        and item.manual_resolved_at is not None
        and now - item.manual_resolved_at >= MISMATCH_GRACE
        and counts.pending > 0
    )
