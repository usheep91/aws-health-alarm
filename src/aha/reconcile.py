from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aha.adapters.health_client import EPOCH, to_snapshot
from aha.config import Config
from aha.domain.models import EventSnapshot, Intent, IntentKind, State, TrackingItem
from aha.domain.state_machine import MISMATCH_GRACE
from aha.domain.observe import observe_with_retry
from aha.domain.repository import Repository
from aha.routing import Router

CURSOR_OVERLAP = timedelta(hours=2)
EVENT_CURSOR = "event"
# TRIAGE/OPEN은 항상 조회한다. RESOLVED_AUTO는 REOPEN_WATCH(해결 후 30일) 동안만 재발 감지를 위해,
# RESOLVED_MANUAL은 불일치가 아직 플래그되지 않았고 MISMATCH_WATCH(유예 기간 + 12시간) 동안만 조회한다.
# 감시 기간이 지난 해결 항목은 Health API 호출을 늘리지 않도록 더 이상 조회하지 않는다.
ENTITY_SWEEP_STATES = (State.TRIAGE, State.OPEN, State.RESOLVED_AUTO, State.RESOLVED_MANUAL)
REOPEN_WATCH = timedelta(days=30)
MISMATCH_WATCH = MISMATCH_GRACE + timedelta(hours=12)
# Lambda 종료 전에 sweep을 멈추기 위한 여유 시간(초).
STOP_MARGIN_SECONDS = 60


def _needs_entity_check(item: TrackingItem, now: datetime) -> bool:
    if item.state in (State.TRIAGE, State.OPEN):
        return True
    if item.resolved_at is None:
        return False
    age = now - item.resolved_at
    if item.state is State.RESOLVED_AUTO:
        return age <= REOPEN_WATCH
    if item.state is State.RESOLVED_MANUAL:
        return not item.verification_mismatch and age <= MISMATCH_WATCH
    return False


@dataclass
class ReconcileResult:
    events: int = 0
    observations: int = 0
    created: int = 0
    completed: bool = True


class Reconciler:
    def __init__(self, health, repo: Repository, publisher, router: Router, cfg: Config) -> None:
        self._health = health
        self._repo = repo
        self._publisher = publisher
        self._router = router
        self._cfg = cfg
        self._time_left: Callable[[], float] | None = None

    def run(self, mode: str, now: datetime, time_left: Callable[[], float] | None = None) -> ReconcileResult:
        """time_left는 남은 실행 시간(초)을 반환한다. 여유가 STOP_MARGIN_SECONDS 미만이면 sweep을 중단한다."""
        self._time_left = time_left
        if mode in ("event", "full"):
            return self._event_sweep(mode, now)
        if mode == "entity":
            return self._entity_sweep(now)
        raise ValueError(f"unknown reconcile mode: {mode}")

    def _out_of_time(self) -> bool:
        return self._time_left is not None and self._time_left() < STOP_MARGIN_SECONDS

    def _unchanged(self, event: dict) -> bool:
        record = self._repo.get_event(event["arn"])
        api_updated = event.get("lastUpdatedTime") or event.get("startTime") or EPOCH
        return record is not None and record.last_updated_time == api_updated

    def _event_sweep(self, mode: str, now: datetime) -> ReconcileResult:
        result = ReconcileResult()
        cursor = self._repo.get_cursor(EVENT_CURSOR) if mode == "event" else None
        since = cursor - CURSOR_OVERLAP if cursor else None
        statuses = ["open", "upcoming"] if since is None else ["open", "upcoming", "closed"]

        for event in self._health.iter_events(updated_since=since, status_codes=statuses):
            if self._out_of_time():
                # 커서를 전진시키지 않으므로 다음 실행이 남은 구간을 다시 본다.
                result.completed = False
                return result
            if self._skip(event):
                continue
            # 메타데이터가 그대로인 이벤트는 건너뛴다(엔티티 변화는 entity sweep 담당).
            # 끝나지 않는 backlog가 매시간 전체 재처리되는 것을 막는다.
            if mode == "event" and self._unchanged(event):
                continue
            result.events += 1
            accounts = self._health.affected_accounts(event["arn"])
            description = self._health.event_description(event["arn"], accounts[0]) if accounts else None
            for account_id in accounts:
                entities = self._health.affected_entities(event["arn"], account_id)
                self._observe(to_snapshot(event, account_id, description, entities), now, result)

        if mode == "event":
            # 실패 시 이 줄에 도달하지 않으므로 다음 실행이 같은 구간을 다시 본다.
            self._repo.set_cursor(EVENT_CURSOR, now)
        return result

    def _skip(self, event: dict) -> bool:
        if event.get("eventScopeCode") == "PUBLIC":
            return True
        return (
            event.get("actionability") == "INFORMATIONAL"
            and event["eventTypeCode"] not in self._cfg.force_track_event_types
        )

    def _entity_sweep(self, now: datetime) -> ReconcileResult:
        result = ReconcileResult()
        items = [i for state in ENTITY_SWEEP_STATES for i in self._repo.list_by_state(state)]
        # 시간 예산이 부족해도 누락 Notice 복구는 반드시 수행되도록 먼저 실행한다.
        self._repair_missing_notices({i.group_id for i in items})
        for item in items:
            if self._out_of_time():
                result.completed = False
                return result
            if not _needs_entity_check(item, now):
                continue
            for arn in sorted(item.event_arns):
                record = self._repo.get_event(arn)
                if record is None:
                    continue
                entities = self._health.affected_entities(arn, item.account_id)
                self._observe(record.to_snapshot(item.account_id, entities, "RECONCILE"), now, result)
        return result

    def _repair_missing_notices(self, group_ids: set[str]) -> None:
        intents = []
        for group_id in sorted(group_ids):
            group = self._repo.get_group(group_id)
            event = self._repo.get_event(group.primary_arn) if group else None
            if event is None:
                continue
            posted = {n.channel_id for n in self._repo.get_notices(group_id)}
            needed = {
                channel
                for item in self._repo.list_group_tracking(group_id)
                if not item.is_terminal
                for channel in self._router.channels_for(item.account_id, event.personas)
            }
            if needed - posted:
                intents.append(Intent(IntentKind.REFRESH, group_id))
        self._publisher.publish(intents)

    def _observe(self, snap: EventSnapshot, now: datetime, result: ReconcileResult) -> None:
        intents = observe_with_retry(snap, self._repo, self._cfg, now)
        result.observations += 1
        result.created += sum(1 for i in intents if i.kind is IntentKind.NEW)
        self._publisher.publish(intents)
