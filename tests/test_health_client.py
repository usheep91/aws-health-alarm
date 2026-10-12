from datetime import UTC, datetime

import boto3
from botocore.stub import Stubber

from aha.adapters.health_client import HealthClient, to_snapshot
from aha.domain.models import Entity

SINCE = datetime(2026, 10, 1, tzinfo=UTC)


def _stubbed():
    client = boto3.client("health", region_name="us-east-1")
    return client, Stubber(client)


def test_iter_events_paginates_with_filter():
    client, stub = _stubbed()
    flt = {"eventStatusCodes": ["open", "upcoming", "closed"], "lastUpdatedTime": {"from": SINCE}}
    stub.add_response("describe_events_for_organization", {"events": [{"arn": "a1"}], "nextToken": "tok1"}, {"filter": flt})
    stub.add_response("describe_events_for_organization", {"events": [{"arn": "a2"}]}, {"filter": flt, "nextToken": "tok1"})
    with stub:
        events = list(HealthClient(client).iter_events(updated_since=SINCE, status_codes=["open", "upcoming", "closed"]))
    assert [e["arn"] for e in events] == ["a1", "a2"]


def test_iter_events_without_since():
    client, stub = _stubbed()
    stub.add_response("describe_events_for_organization", {"events": []}, {"filter": {"eventStatusCodes": ["open"]}})
    with stub:
        assert list(HealthClient(client).iter_events(updated_since=None, status_codes=["open"])) == []


def test_affected_accounts():
    client, stub = _stubbed()
    stub.add_response(
        "describe_affected_accounts_for_organization",
        {"affectedAccounts": ["111122223333", "444455556666"]},
        {"eventArn": "a1"},
    )
    with stub:
        assert HealthClient(client).affected_accounts("a1") == ["111122223333", "444455556666"]


def test_event_description():
    client, stub = _stubbed()
    stub.add_response(
        "describe_event_details_for_organization",
        {"successfulSet": [{"awsAccountId": "111122223333", "eventDescription": {"latestDescription": "hello"}}], "failedSet": []},
        {"organizationEventDetailFilters": [{"eventArn": "a1", "awsAccountId": "111122223333"}]},
    )
    with stub:
        assert HealthClient(client).event_description("a1", "111122223333") == "hello"


def test_affected_entities():
    client, stub = _stubbed()
    stub.add_response(
        "describe_affected_entities_for_organization",
        {"entities": [{"entityValue": "db-1", "statusCode": "PENDING"}, {"entityValue": "db-2"}]},
        {"organizationEntityAccountFilters": [{"eventArn": "a1", "awsAccountId": "111122223333"}]},
    )
    with stub:
        assert HealthClient(client).affected_entities("a1", "111122223333") == [
            Entity("db-1", "PENDING"),
            Entity("db-2", "UNKNOWN"),
        ]


def test_to_snapshot_maps_fields():
    event = {
        "arn": "a1",
        "service": "RDS",
        "eventTypeCode": "AWS_RDS_PLANNED_LIFECYCLE_EVENT",
        "eventTypeCategory": "scheduledChange",
        "region": "ap-northeast-2",
        "statusCode": "upcoming",
        "startTime": datetime(2026, 11, 30, tzinfo=UTC),
        "lastUpdatedTime": SINCE,
        "actionability": "ACTION_REQUIRED",
        "personas": ["OPERATIONS"],
    }
    snap = to_snapshot(event, "111122223333", "desc", [Entity("db-1", "PENDING")])
    assert (snap.event_arn, snap.account_id, snap.category, snap.actionability) == (
        "a1", "111122223333", "scheduledChange", "ACTION_REQUIRED"
    )
    assert snap.personas == ("OPERATIONS",)
    assert snap.entities == (Entity("db-1", "PENDING"),)
    assert snap.end_time is None
