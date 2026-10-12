from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from aha.adapters.health_client import to_snapshot
from aha.config import Config
from aha.domain.models import EventSnapshot, Intent, IntentKind, State
from aha.domain.observe import observe_with_retry
from aha.domain.repository import Repository
from aha.routing import Router

CURSOR_OVERLAP = timedelta(hours=2)
EVENT_CURSOR = "event"
# RESOLVED_AUTO는 재발 감지, RESOLVED_MANUAL은 불일치 검증을 위해 계속 조회한다.
ENTITY_SWEEP_STATES = (State.TRIAGE, State.OPEN, State.RESOLVED_AUTO, State.RESOLVED_MANUAL)


@dataclass
class ReconcileResult:
    events: int = 0
    observations: int = 0
    created: int = 0


class Reconciler:
    def __init__(self, health, repo: Repository, publisher, router: Router, cfg: Config) -> None:
        self._health = health
        self._repo = repo
        self._publisher = publisher
        self._router = router
        self._cfg = cfg

    def run(self, mode: str, now: datetime) -> ReconcileResult:
        if mode in ("event", "full"):
            return self._event_sweep(mode, now)
        if mode == "entity":
            return self._entity_sweep(now)
        raise ValueError(f"unknown reconcile mode: {mode}")

    def _event_sweep(self, mode: str, now: datetime) -> ReconcileResult:
        result = ReconcileResult()
        cursor = self._repo.get_cursor(EVENT_CURSOR) if mode == "event" else None
        since = cursor - CURSOR_OVERLAP if cursor else None
        statuses = ["open", "upcoming"] if since is None else ["open", "upcoming", "closed"]

        for event in self._health.iter_events(updated_since=since, status_codes=statuses):
            if self._skip(event):
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
        for item in items:
            if item.state is State.RESOLVED_MANUAL and item.verification_mismatch:
                continue
            for arn in sorted(item.event_arns):
                record = self._repo.get_event(arn)
                if record is None:
                    continue
                entities = self._health.affected_entities(arn, item.account_id)
                self._observe(record.to_snapshot(item.account_id, entities, "RECONCILE"), now, result)
        self._repair_missing_notices({i.group_id for i in items})
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
