from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from datetime import date, datetime

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from aha.domain.models import (
    AuditEntry,
    DueDateSource,
    Entity,
    EntityCounts,
    EventGroup,
    EventRecord,
    Notice,
    State,
    Tier,
    TrackingItem,
    split_tracking_id,
)
from aha.domain.repository import ConcurrentModification

STATE_INDEX = "gsi-state"


class DynamoRepository:
    def __init__(self, table) -> None:
        self._t = table

    # --- Event ---
    def get_event(self, event_arn: str) -> EventRecord | None:
        item = self._get(f"EVT#{event_arn}", "META")
        return _event_from(item) if item else None

    def put_event(self, record: EventRecord) -> None:
        self._t.put_item(Item={"pk": f"EVT#{record.event_arn}", "sk": "META", **_event_to(record)})

    # --- Group ---
    def get_group(self, group_id: str) -> EventGroup | None:
        item = self._get(f"GRP#{group_id}", "META")
        if not item:
            return None
        return EventGroup(group_id, item["correlation_key"], item["primary_arn"], set(item["event_arns"]))

    def put_group(self, group: EventGroup) -> None:
        self._t.update_item(
            Key={"pk": f"GRP#{group.group_id}", "sk": "META"},
            UpdateExpression=(
                "SET correlation_key = if_not_exists(correlation_key, :k), "
                "primary_arn = if_not_exists(primary_arn, :p) ADD event_arns :a"
            ),
            ExpressionAttributeValues={":k": group.correlation_key, ":p": group.primary_arn, ":a": set(group.event_arns)},
        )

    # --- Tracking ---
    def get_tracking(self, tracking_id: str) -> TrackingItem | None:
        group_id, account_id = split_tracking_id(tracking_id)
        item = self._get(f"GRP#{group_id}", f"TRK#{account_id}")
        return _tracking_from(item) if item else None

    def create_tracking(self, item: TrackingItem) -> bool:
        try:
            self._t.put_item(Item=_tracking_to(item, version=1), ConditionExpression="attribute_not_exists(pk)")
        except ClientError as e:
            if _conditional_failed(e):
                return False
            raise
        item.version = 1
        return True

    def save_tracking(self, item: TrackingItem) -> None:
        try:
            self._t.put_item(
                Item=_tracking_to(item, version=item.version + 1),
                ConditionExpression="version = :v",
                ExpressionAttributeValues={":v": item.version},
            )
        except ClientError as e:
            if _conditional_failed(e):
                raise ConcurrentModification(item.tracking_id) from e
            raise
        item.version += 1

    def list_group_tracking(self, group_id: str) -> list[TrackingItem]:
        cond = Key("pk").eq(f"GRP#{group_id}") & Key("sk").begins_with("TRK#")
        return [_tracking_from(i) for i in self._query(cond)]

    def list_by_state(self, state: State) -> list[TrackingItem]:
        return [_tracking_from(i) for i in self._query(Key("gsi1pk").eq(f"STATE#{state.value}"), index=STATE_INDEX)]

    # --- Entity ---
    def upsert_entities(self, tracking_id: str, entities: Iterable[Entity], now: datetime) -> None:
        with self._t.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for e in entities:
                batch.put_item(
                    Item={"pk": f"TRK#{tracking_id}", "sk": f"ENT#{e.value}", "status": e.status, "last_seen_at": now.isoformat()}
                )

    def list_entities(self, tracking_id: str) -> list[Entity]:
        cond = Key("pk").eq(f"TRK#{tracking_id}") & Key("sk").begins_with("ENT#")
        return [Entity(i["sk"].removeprefix("ENT#"), i["status"]) for i in self._query(cond)]

    # --- Audit ---
    def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        self._t.put_item(
            Item={
                "pk": f"TRK#{tracking_id}",
                "sk": f"LOG#{entry.at.isoformat()}#{uuid.uuid4().hex[:8]}",
                "actor": entry.actor,
                "action": entry.action,
                "before": entry.before,
                "after": entry.after,
                "detail": entry.detail,
            }
        )

    def list_audit(self, tracking_id: str) -> list[AuditEntry]:
        cond = Key("pk").eq(f"TRK#{tracking_id}") & Key("sk").begins_with("LOG#")
        return [
            AuditEntry(datetime.fromisoformat(i["sk"].split("#")[1]), i["actor"], i["action"], i.get("before"), i.get("after"), i.get("detail"))
            for i in self._query(cond)
        ]

    # --- Notice ---
    def get_notices(self, group_id: str) -> list[Notice]:
        cond = Key("pk").eq(f"GRP#{group_id}") & Key("sk").begins_with("NTC#")
        return [Notice(group_id, i["sk"].removeprefix("NTC#"), i["ts"], i["posted_channel_id"]) for i in self._query(cond)]

    def put_notice(self, notice: Notice) -> None:
        self._t.put_item(
            Item={
                "pk": f"GRP#{notice.group_id}",
                "sk": f"NTC#{notice.channel_id}",
                "ts": notice.ts,
                "posted_channel_id": notice.posted_channel_id,
            }
        )

    # --- Cursor ---
    def get_cursor(self, name: str) -> datetime | None:
        item = self._get("CURSOR", name)
        return datetime.fromisoformat(item["value"]) if item else None

    def set_cursor(self, name: str, value: datetime) -> None:
        self._t.put_item(Item={"pk": "CURSOR", "sk": name, "value": value.isoformat()})

    # --- helpers ---
    def _get(self, pk: str, sk: str) -> dict | None:
        return self._t.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")

    def _query(self, condition, index: str | None = None) -> Iterator[dict]:
        kwargs: dict = {"KeyConditionExpression": condition}
        if index:
            kwargs["IndexName"] = index
        else:
            kwargs["ConsistentRead"] = True
        while True:
            resp = self._t.query(**kwargs)
            yield from resp.get("Items", [])
            if "LastEvaluatedKey" not in resp:
                return
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _conditional_failed(e: ClientError) -> bool:
    return e.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _event_to(r: EventRecord) -> dict:
    return {
        "group_id": r.group_id,
        "service": r.service,
        "event_type_code": r.event_type_code,
        "category": r.category,
        "event_region": r.event_region,
        "status_code": r.status_code,
        "start_time": _iso(r.start_time),
        "end_time": _iso(r.end_time),
        "last_updated_time": r.last_updated_time.isoformat(),
        "actionability": r.actionability,
        "personas": list(r.personas),
        "description": r.description,
        "description_hash": r.description_hash,
        "dynamic_status": r.dynamic_status,
    }


def _event_from(i: dict) -> EventRecord:
    return EventRecord(
        event_arn=i["pk"].removeprefix("EVT#"),
        group_id=i["group_id"],
        service=i["service"],
        event_type_code=i["event_type_code"],
        category=i["category"],
        event_region=i["event_region"],
        status_code=i["status_code"],
        start_time=_dt(i.get("start_time")),
        end_time=_dt(i.get("end_time")),
        last_updated_time=datetime.fromisoformat(i["last_updated_time"]),
        actionability=i.get("actionability"),
        personas=tuple(i.get("personas") or ()),
        description=i.get("description"),
        description_hash=i.get("description_hash"),
        dynamic_status=bool(i["dynamic_status"]),
    )


def _tracking_to(t: TrackingItem, version: int) -> dict:
    return {
        "pk": f"GRP#{t.group_id}",
        "sk": f"TRK#{t.account_id}",
        "gsi1pk": f"STATE#{t.state.value}",
        "gsi1sk": f"{t.due_date.isoformat()}#{t.tracking_id}",
        "group_id": t.group_id,
        "account_id": t.account_id,
        "tier": t.tier.value,
        "state": t.state.value,
        "due_date": t.due_date.isoformat(),
        "due_date_source": t.due_date_source.value,
        "created_at": t.created_at.isoformat(),
        "event_arns": sorted(t.event_arns),
        "aws_due_date": _iso(t.aws_due_date),
        "assignee": t.assignee,
        "reminders_sent": sorted(t.reminders_sent),
        "manual_completion_required": t.manual_completion_required,
        "verification_mismatch": t.verification_mismatch,
        "counts": {"pending": t.counts.pending, "resolved": t.counts.resolved, "total": t.counts.total},
        "resolved_at": _iso(t.resolved_at),
        "manual_resolved_at": _iso(t.manual_resolved_at),
        "version": version,
    }


def _tracking_from(i: dict) -> TrackingItem:
    c = i["counts"]
    return TrackingItem(
        group_id=i["group_id"],
        account_id=i["account_id"],
        tier=Tier(i["tier"]),
        state=State(i["state"]),
        due_date=date.fromisoformat(i["due_date"]),
        due_date_source=DueDateSource(i["due_date_source"]),
        created_at=datetime.fromisoformat(i["created_at"]),
        event_arns=set(i.get("event_arns") or ()),
        aws_due_date=_d(i.get("aws_due_date")),
        assignee=i.get("assignee"),
        reminders_sent=set(i.get("reminders_sent") or ()),
        manual_completion_required=bool(i["manual_completion_required"]),
        verification_mismatch=bool(i["verification_mismatch"]),
        counts=EntityCounts(int(c["pending"]), int(c["resolved"]), int(c["total"])),
        resolved_at=_dt(i.get("resolved_at")),
        manual_resolved_at=_dt(i.get("manual_resolved_at")),
        version=int(i["version"]),
    )
