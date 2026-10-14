from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class Tier(StrEnum):
    REQUIRED = "TIER1"
    REVIEW = "TIER2"
    EXCLUDED = "EXCLUDED"


class State(StrEnum):
    TRIAGE = "TRIAGE"
    OPEN = "OPEN"
    RESOLVED_AUTO = "RESOLVED_AUTO"
    RESOLVED_MANUAL = "RESOLVED_MANUAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CLOSED_BY_AWS = "CLOSED_BY_AWS"


TERMINAL_STATES = frozenset(
    {State.RESOLVED_AUTO, State.RESOLVED_MANUAL, State.NOT_APPLICABLE, State.CLOSED_BY_AWS}
)


class DueDateSource(StrEnum):
    AWS = "AWS"
    DEFAULT_SLA = "DEFAULT_SLA"
    MANUAL = "MANUAL"


class IntentKind(StrEnum):
    NEW = "NEW"
    CONTENT_UPDATED = "CONTENT_UPDATED"
    DUE_CHANGED = "DUE_CHANGED"
    PROGRESS = "PROGRESS"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    CLOSED_BY_AWS = "CLOSED_BY_AWS"
    MISMATCH = "MISMATCH"
    PROMOTED = "PROMOTED"
    ACTION = "ACTION"
    REFRESH = "REFRESH"
    EXPORT = "EXPORT"
    REMINDER = "REMINDER"


# 리소스 상태 중 이 값이 하나라도 있으면 해당 이벤트는 동적 상태 업데이트를 지원한다.
DYNAMIC_STATUSES = frozenset({"PENDING", "RESOLVED"})


@dataclass(frozen=True)
class Entity:
    value: str
    status: str  # IMPAIRED | UNIMPAIRED | UNKNOWN | PENDING | RESOLVED


@dataclass(frozen=True)
class EntityCounts:
    pending: int = 0
    resolved: int = 0
    total: int = 0

    @classmethod
    def of(cls, entities: Iterable[Entity]) -> EntityCounts:
        items = list(entities)
        return cls(
            pending=sum(e.status == "PENDING" for e in items),
            resolved=sum(e.status == "RESOLVED" for e in items),
            total=len(items),
        )


@dataclass(frozen=True)
class EventSnapshot:
    event_arn: str
    account_id: str
    service: str
    event_type_code: str
    category: str
    event_region: str
    status_code: str
    start_time: datetime | None
    end_time: datetime | None
    last_updated_time: datetime
    actionability: str | None
    personas: tuple[str, ...]
    description: str | None
    entities: tuple[Entity, ...]
    source: str


@dataclass
class EventRecord:
    event_arn: str
    group_id: str
    service: str
    event_type_code: str
    category: str
    event_region: str
    status_code: str
    start_time: datetime | None
    end_time: datetime | None
    last_updated_time: datetime
    actionability: str | None
    personas: tuple[str, ...]
    description: str | None
    description_hash: str | None
    dynamic_status: bool

    def to_snapshot(self, account_id: str, entities: Iterable[Entity], source: str) -> EventSnapshot:
        return EventSnapshot(
            event_arn=self.event_arn,
            account_id=account_id,
            service=self.service,
            event_type_code=self.event_type_code,
            category=self.category,
            event_region=self.event_region,
            status_code=self.status_code,
            start_time=self.start_time,
            end_time=self.end_time,
            last_updated_time=self.last_updated_time,
            actionability=self.actionability,
            personas=self.personas,
            description=None,
            entities=tuple(entities),
            source=source,
        )


@dataclass
class EventGroup:
    group_id: str
    correlation_key: str
    primary_arn: str
    event_arns: set[str] = field(default_factory=set)


def tracking_id_of(group_id: str, account_id: str) -> str:
    return f"{group_id}#{account_id}"


def split_tracking_id(tracking_id: str) -> tuple[str, str]:
    group_id, account_id = tracking_id.split("#", 1)
    return group_id, account_id


@dataclass
class TrackingItem:
    group_id: str
    account_id: str
    tier: Tier
    state: State
    due_date: date
    due_date_source: DueDateSource
    created_at: datetime
    event_arns: set[str] = field(default_factory=set)
    aws_due_date: date | None = None
    assignee: str | None = None
    reminders_sent: set[str] = field(default_factory=set)
    manual_completion_required: bool = False
    verification_mismatch: bool = False
    counts: EntityCounts = field(default_factory=EntityCounts)
    resolved_at: datetime | None = None
    manual_resolved_at: datetime | None = None
    version: int = 0

    @property
    def tracking_id(self) -> str:
        return tracking_id_of(self.group_id, self.account_id)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass
class Notice:
    group_id: str
    channel_id: str  # 라우팅상 채널
    ts: str
    posted_channel_id: str  # 실제 게시 채널 (대체 게시 시 default_channel)


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: str
    action: str
    before: str | None
    after: str | None
    detail: str | None = None


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    group_id: str
    tracking_id: str | None = None
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"kind": self.kind.value, "group_id": self.group_id, "tracking_id": self.tracking_id, "data": self.data},
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, body: str) -> Intent:
        raw = json.loads(body)
        return cls(IntentKind(raw["kind"]), raw["group_id"], raw.get("tracking_id"), raw.get("data") or {})
