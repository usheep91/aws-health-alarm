from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from aha.domain.models import AuditEntry, DueDateSource, Intent, IntentKind, State, Tier, TrackingItem
from aha.domain.repository import ConcurrentModification, Repository

MAX_ATTEMPTS = 3


class InvalidAction(Exception):
    """사용자 액션을 적용할 수 없음 (메시지는 Slack에 그대로 노출된다)."""


class ActionKind(StrEnum):
    ASSIGN = "ASSIGN"
    SET_DUE = "SET_DUE"
    RESOLVE_MANUAL = "RESOLVE_MANUAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    TRIAGE_REQUIRED = "TRIAGE_REQUIRED"


def _validate_args(kind: ActionKind, assignee: str | None, due: date | None, reason: str | None) -> None:
    if kind is ActionKind.ASSIGN and not assignee:
        raise InvalidAction("담당자를 선택하세요")
    if kind is ActionKind.SET_DUE and due is None:
        raise InvalidAction("Due Date를 입력하세요")
    if kind is ActionKind.NOT_APPLICABLE and (not reason or not reason.strip()):
        raise InvalidAction("사유를 입력하세요")


def apply_action(
    repo: Repository,
    tracking_id: str,
    kind: ActionKind,
    actor: str,
    now: datetime,
    *,
    assignee: str | None = None,
    due: date | None = None,
    reason: str | None = None,
) -> TrackingItem:
    _validate_args(kind, assignee, due, reason)
    item = repo.get_tracking(tracking_id)
    if item is None:
        raise InvalidAction(f"추적 항목을 찾을 수 없습니다: {tracking_id}")
    if item.is_terminal:
        raise InvalidAction("이미 종결된 항목입니다")
    before = item.state

    if kind is ActionKind.ASSIGN:
        item.assignee = assignee
    elif kind is ActionKind.SET_DUE:
        item.due_date, item.due_date_source = due, DueDateSource.MANUAL
        item.reminders_sent = set()
    elif kind is ActionKind.RESOLVE_MANUAL:
        item.state = State.RESOLVED_MANUAL
        item.resolved_at = item.manual_resolved_at = now
    elif kind is ActionKind.NOT_APPLICABLE:
        item.state, item.resolved_at = State.NOT_APPLICABLE, now
    elif kind is ActionKind.TRIAGE_REQUIRED:
        if item.state is not State.TRIAGE:
            raise InvalidAction("검토 대기 상태가 아닙니다")
        item.state, item.tier = State.OPEN, Tier.REQUIRED

    repo.save_tracking(item)
    repo.append_audit(
        tracking_id,
        AuditEntry(now, actor, kind.value, before.value, item.state.value, _audit_detail(kind, assignee, due, reason)),
    )
    return item


def _audit_detail(kind: ActionKind, assignee: str | None, due: date | None, reason: str | None) -> str | None:
    if kind is ActionKind.ASSIGN:
        return assignee
    if kind is ActionKind.SET_DUE and due is not None:
        return f"{due.isoformat()} ({reason})" if reason else due.isoformat()
    return reason


def apply_group_action(
    repo: Repository,
    tracking_ids: list[str],
    kind: ActionKind,
    actor: str,
    now: datetime,
    *,
    channel: str,
    assignee: str | None = None,
    due: date | None = None,
    reason: str | None = None,
) -> list[Intent]:
    _validate_args(kind, assignee, due, reason)
    applied: list[TrackingItem] = []
    for tracking_id in tracking_ids:
        try:
            applied.append(_apply_with_retry(repo, tracking_id, kind, actor, now, assignee, due, reason))
        except (InvalidAction, ConcurrentModification):
            if len(tracking_ids) == 1:
                raise
    if not applied:
        return []
    return [
        Intent(
            IntentKind.ACTION,
            applied[0].group_id,
            None,
            {
                "action": kind.value,
                "actor": actor,
                "accounts": [i.account_id for i in applied],
                "assignee": assignee,
                "due": due.isoformat() if due else None,
                "reason": reason,
                "channel": channel,
            },
        )
    ]


def _apply_with_retry(
    repo: Repository,
    tracking_id: str,
    kind: ActionKind,
    actor: str,
    now: datetime,
    assignee: str | None,
    due: date | None,
    reason: str | None,
) -> TrackingItem:
    for _ in range(MAX_ATTEMPTS - 1):
        try:
            return apply_action(repo, tracking_id, kind, actor, now, assignee=assignee, due=due, reason=reason)
        except ConcurrentModification:
            continue
    return apply_action(repo, tracking_id, kind, actor, now, assignee=assignee, due=due, reason=reason)
