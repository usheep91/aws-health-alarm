from __future__ import annotations

import copy
import hashlib
from datetime import datetime

from aha.config import Config
from aha.domain.classify import classify
from aha.domain.due_date import compute_due, to_local_date
from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import (
    DYNAMIC_STATUSES,
    AuditEntry,
    DueDateSource,
    EntityCounts,
    EventGroup,
    EventRecord,
    EventSnapshot,
    Intent,
    IntentKind,
    State,
    Tier,
    TrackingItem,
    tracking_id_of,
)
from aha.domain.repository import ConcurrentModification, Repository
from aha.domain.state_machine import needs_mismatch_flag, next_state

# OPEN/TRIAGE 대상은 RESOLVED_AUTO에서 재오픈할 때만 발생한다.
STATE_CHANGE_INTENTS = {
    State.RESOLVED_AUTO: IntentKind.RESOLVED,
    State.CLOSED_BY_AWS: IntentKind.CLOSED_BY_AWS,
    State.OPEN: IntentKind.REOPENED,
    State.TRIAGE: IntentKind.REOPENED,
}


def observe(snap: EventSnapshot, repo: Repository, cfg: Config, now: datetime) -> list[Intent]:
    """스냅샷 1건(eventArn × 계정)을 상태에 반영하고, 알림이 필요한 변화만 Intent로 돌려준다."""
    tier, _ = classify(snap, cfg)
    if tier is Tier.EXCLUDED:
        return []
    stored, record, content_changed, stale = _upsert_event(snap, repo, cfg)
    group = _ensure_group(snap, record, repo, cfg)
    tracking_id = tracking_id_of(group.group_id, snap.account_id)
    repo.upsert_entities(tracking_id, snap.entities, now)
    counts = EntityCounts.of(repo.list_entities(tracking_id))

    # 오래된 스냅샷이면 저장된 이벤트 메타데이터로 기한/상태를 계산한다.
    effective = record.to_snapshot(snap.account_id, snap.entities, snap.source) if stale else snap
    intents = [Intent(IntentKind.CONTENT_UPDATED, group.group_id)] if content_changed else []
    item = repo.get_tracking(tracking_id)
    if item is None:
        intents += _create(effective, record, group, tier, counts, repo, cfg, now)
    else:
        intents += _update(item, effective, record, tier, counts, repo, cfg, now)
    # 추적 항목이 저장된 뒤에 이벤트를 기록해야 재시도 시 CONTENT_UPDATED가 유실되지 않는다.
    if record != stored:
        repo.put_event(record)
    return intents


def observe_with_retry(
    snap: EventSnapshot, repo: Repository, cfg: Config, now: datetime, attempts: int = 3
) -> list[Intent]:
    for attempt in range(attempts):
        try:
            return observe(snap, repo, cfg, now)
        except ConcurrentModification:
            if attempt == attempts - 1:
                raise
    return []


def _hash(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest() if text else None


def _upsert_event(
    snap: EventSnapshot, repo: Repository, cfg: Config
) -> tuple[EventRecord | None, EventRecord, bool, bool]:
    """(저장된 레코드, 적용할 레코드, 내용 변경 여부, 스냅샷이 오래됨 여부). 쓰기는 하지 않는다."""
    prev = repo.get_event(snap.event_arn)
    dynamic = any(e.status in DYNAMIC_STATUSES for e in snap.entities)
    if prev is not None and snap.last_updated_time < prev.last_updated_time:
        record = copy.deepcopy(prev)
        record.dynamic_status = prev.dynamic_status or dynamic
        return prev, record, False, True

    description = snap.description if snap.description is not None else (prev.description if prev else None)
    description_hash = _hash(description)
    changed = prev is not None and prev.description_hash is not None and description_hash != prev.description_hash
    record = EventRecord(
        event_arn=snap.event_arn,
        group_id=prev.group_id if prev else group_id_for(correlation_key(snap, cfg.timezone)),
        service=snap.service,
        event_type_code=snap.event_type_code,
        category=snap.category,
        event_region=snap.event_region,
        status_code=snap.status_code,
        start_time=snap.start_time,
        end_time=snap.end_time,
        last_updated_time=snap.last_updated_time,
        actionability=snap.actionability,
        personas=snap.personas,
        description=description,
        description_hash=description_hash,
        dynamic_status=dynamic or (prev.dynamic_status if prev else False),
    )
    return prev, record, changed, False


def _ensure_group(snap: EventSnapshot, record: EventRecord, repo: Repository, cfg: Config) -> EventGroup:
    group = repo.get_group(record.group_id)
    if group is None:
        group = EventGroup(record.group_id, correlation_key(snap, cfg.timezone), snap.event_arn, {snap.event_arn})
    elif snap.event_arn in group.event_arns:
        return group
    else:
        group.event_arns.add(snap.event_arn)
    repo.put_group(group)
    return group


def _create(snap, record, group, tier, counts, repo, cfg, now) -> list[Intent]:
    due, source = compute_due(snap, to_local_date(now, cfg.timezone), cfg)
    item = TrackingItem(
        group_id=group.group_id,
        account_id=snap.account_id,
        tier=tier,
        state=State.OPEN if tier is Tier.REQUIRED else State.TRIAGE,
        due_date=due,
        due_date_source=source,
        created_at=now,
        event_arns={snap.event_arn},
        aws_due_date=due if source is DueDateSource.AWS else None,
        manual_completion_required=not record.dynamic_status,
        counts=counts,
    )
    initial = next_state(item, counts, record.dynamic_status, snap.status_code)
    if initial is not item.state:
        item.state, item.resolved_at = initial, now
    if not repo.create_tracking(item):
        raise ConcurrentModification(item.tracking_id)
    repo.append_audit(item.tracking_id, AuditEntry(now, "system", "CREATED", None, item.state.value))
    # 발견 시점에 이미 종결된 항목은 알릴 필요가 없다.
    return [] if item.is_terminal else [Intent(IntentKind.NEW, group.group_id, item.tracking_id)]


def _update(item, snap, record, tier, counts, repo, cfg, now) -> list[Intent]:
    original = copy.deepcopy(item)
    intents: list[Intent] = []
    item.event_arns.add(snap.event_arn)
    item.manual_completion_required = not record.dynamic_status

    if tier is Tier.REQUIRED and item.tier is Tier.REVIEW:
        item.tier = Tier.REQUIRED
        if item.state is State.TRIAGE:
            item.state = State.OPEN
            intents.append(Intent(IntentKind.PROMOTED, item.group_id, item.tracking_id))

    if not item.is_terminal:
        intents += _sync_due_date(item, snap, cfg)

    new_state = next_state(item, counts, record.dynamic_status, snap.status_code)
    if new_state is not item.state:
        item.state = new_state
        item.resolved_at = now if item.is_terminal else None
        intents.append(Intent(STATE_CHANGE_INTENTS[new_state], item.group_id, item.tracking_id))
    elif not item.is_terminal and counts.pending != item.counts.pending:
        intents.append(
            Intent(
                IntentKind.PROGRESS,
                item.group_id,
                item.tracking_id,
                {"old_pending": item.counts.pending, "new_pending": counts.pending, "total": counts.total},
            )
        )
    item.counts = counts

    if needs_mismatch_flag(item, counts, now):
        item.verification_mismatch = True
        intents.append(Intent(IntentKind.MISMATCH, item.group_id, item.tracking_id, {"pending": counts.pending}))

    if item != original:
        repo.save_tracking(item)
        if item.state is not original.state:
            repo.append_audit(
                item.tracking_id,
                AuditEntry(now, "system", "STATE_CHANGE", original.state.value, item.state.value),
            )
    return intents


def _sync_due_date(item: TrackingItem, snap: EventSnapshot, cfg: Config) -> list[Intent]:
    due, source = compute_due(snap, to_local_date(item.created_at, cfg.timezone), cfg)
    aws_due = due if source is DueDateSource.AWS else None

    if item.due_date_source is DueDateSource.MANUAL:
        if aws_due is None or aws_due == item.aws_due_date:
            return []
        old, item.aws_due_date = item.aws_due_date, aws_due
        return [_due_intent(item, old, aws_due, DueDateSource.AWS, kept_manual=True)]

    if due == item.due_date and source is item.due_date_source:
        return []
    old = item.due_date
    item.due_date, item.due_date_source, item.aws_due_date = due, source, aws_due
    if due == old:
        return []
    # 기한이 바뀌면 다음 리마인더 실행에서 새 기한 기준으로 기준선을 다시 잡는다.
    item.reminders_sent = set()
    return [_due_intent(item, old, due, source, kept_manual=False)]


def _due_intent(item, old, new, source, kept_manual) -> Intent:
    return Intent(
        IntentKind.DUE_CHANGED,
        item.group_id,
        item.tracking_id,
        {
            "old": old.isoformat() if old else None,
            "new": new.isoformat(),
            "source": source.value,
            "kept_manual": kept_manual,
        },
    )
