from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
from botocore.config import Config as BotoConfig

from aha.domain.models import Entity, EventSnapshot

HEALTH_REGION = "us-east-1"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class HealthClient:
    """AWS Health Organizational view 읽기 전용 래퍼 (위임 관리자 계정에서 호출)."""

    def __init__(self, client=None) -> None:
        self._client = client or boto3.client(
            "health",
            region_name=HEALTH_REGION,
            config=BotoConfig(retries={"mode": "adaptive", "max_attempts": 10}),
        )

    def iter_events(self, *, updated_since: datetime | None, status_codes: list[str]) -> Iterator[dict]:
        flt: dict = {"eventStatusCodes": list(status_codes)}
        if updated_since is not None:
            flt["lastUpdatedTime"] = {"from": updated_since}
        for page in self._client.get_paginator("describe_events_for_organization").paginate(filter=flt):
            yield from page.get("events", [])

    def affected_accounts(self, event_arn: str) -> list[str]:
        accounts: list[str] = []
        for page in self._client.get_paginator("describe_affected_accounts_for_organization").paginate(eventArn=event_arn):
            accounts += page.get("affectedAccounts", [])
        return accounts

    def event_description(self, event_arn: str, account_id: str) -> str | None:
        resp = self._client.describe_event_details_for_organization(
            organizationEventDetailFilters=[{"eventArn": event_arn, "awsAccountId": account_id}]
        )
        for detail in resp.get("successfulSet", []):
            return detail.get("eventDescription", {}).get("latestDescription")
        return None

    def affected_entities(self, event_arn: str, account_id: str) -> list[Entity]:
        entities: list[Entity] = []
        paginator = self._client.get_paginator("describe_affected_entities_for_organization")
        for page in paginator.paginate(organizationEntityAccountFilters=[{"eventArn": event_arn, "awsAccountId": account_id}]):
            entities += [
                Entity(e["entityValue"], e.get("statusCode", "UNKNOWN")) for e in page.get("entities", []) if "entityValue" in e
            ]
        return entities


def to_snapshot(
    event: dict, account_id: str, description: str | None, entities: list[Entity], source: str = "RECONCILE"
) -> EventSnapshot:
    return EventSnapshot(
        event_arn=event["arn"],
        account_id=account_id,
        service=event.get("service", ""),
        event_type_code=event["eventTypeCode"],
        category=event.get("eventTypeCategory", ""),
        event_region=event.get("region", "global"),
        status_code=event.get("statusCode", "open"),
        start_time=event.get("startTime"),
        end_time=event.get("endTime"),
        last_updated_time=event.get("lastUpdatedTime") or event.get("startTime") or EPOCH,
        actionability=event.get("actionability"),
        personas=tuple(event.get("personas") or ()),
        description=description,
        entities=tuple(entities),
        source=source,
    )
