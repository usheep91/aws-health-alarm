from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from aha.adapters.repo_dynamodb import DynamoRepository
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
)
from aha.domain.repository import ConcurrentModification
from tests.dynamo_util import create_state_table
from tests.factories import ACCOUNT, ARN, NOW, OTHER_ACCOUNT, make_item
from tests.fakes import InMemoryRepository


@pytest.fixture(params=["memory", "dynamo"])
def repo(request):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    with mock_aws():
        table = create_state_table(boto3.resource("dynamodb", region_name="ap-northeast-2"))
        yield DynamoRepository(table)


def _record(**overrides):
    base = dict(
        event_arn=ARN,
        group_id="g1",
        service="RDS",
        event_type_code="AWS_RDS_PLANNED_LIFECYCLE_EVENT",
        category="scheduledChange",
        event_region="ap-northeast-2",
        status_code="upcoming",
        start_time=datetime(2026, 11, 30, tzinfo=UTC),
        end_time=None,
        last_updated_time=datetime(2026, 10, 1, tzinfo=UTC),
        actionability="ACTION_REQUIRED",
        personas=("OPERATIONS", "SECURITY"),
        description="desc",
        description_hash="h",
        dynamic_status=True,
    )
    base.update(overrides)
    return EventRecord(**base)


def test_event_roundtrip(repo):
    repo.put_event(_record())
    assert repo.get_event(ARN) == _record()
    assert repo.get_event("missing") is None


def test_group_put_merges_arns(repo):
    repo.put_group(EventGroup("g1", "key", "arn-1", {"arn-1"}))
    repo.put_group(EventGroup("g1", "key", "arn-2", {"arn-2"}))
    group = repo.get_group("g1")
    assert (group.primary_arn, group.event_arns) == ("arn-1", {"arn-1", "arn-2"})


def test_create_tracking_is_conditional(repo):
    item = make_item()
    assert repo.create_tracking(item) is True
    assert item.version == 1
    assert repo.create_tracking(make_item()) is False


def test_full_tracking_roundtrip(repo):
    item = make_item(
        tier=Tier.REVIEW,
        state=State.TRIAGE,
        due_date_source=DueDateSource.MANUAL,
        event_arns={"arn-1", "arn-2"},
        aws_due_date=date(2026, 12, 1),
        assignee="U2",
        reminders_sent={"D-30"},
        manual_completion_required=True,
        verification_mismatch=True,
        counts=EntityCounts(1, 2, 3),
        resolved_at=NOW,
        manual_resolved_at=NOW,
    )
    repo.create_tracking(item)
    assert repo.get_tracking(item.tracking_id) == item


def test_save_tracking_detects_conflict(repo):
    repo.create_tracking(make_item())
    first, second = repo.get_tracking(f"g1#{ACCOUNT}"), repo.get_tracking(f"g1#{ACCOUNT}")
    first.assignee = "U1"
    repo.save_tracking(first)
    assert first.version == 2
    second.assignee = "U2"
    with pytest.raises(ConcurrentModification):
        repo.save_tracking(second)


def test_list_group_and_state(repo):
    repo.create_tracking(make_item(account_id=OTHER_ACCOUNT, due_date=date(2026, 11, 1)))
    repo.create_tracking(make_item(account_id=ACCOUNT, due_date=date(2026, 12, 1)))
    repo.create_tracking(make_item(group_id="g2", state=State.TRIAGE))
    assert [i.account_id for i in repo.list_group_tracking("g1")] == [ACCOUNT, OTHER_ACCOUNT]
    assert [i.account_id for i in repo.list_by_state(State.OPEN)] == [OTHER_ACCOUNT, ACCOUNT]
    assert [i.group_id for i in repo.list_by_state(State.TRIAGE)] == ["g2"]


def test_entities_upsert_overwrites(repo):
    tid = f"g1#{ACCOUNT}"
    repo.upsert_entities(tid, [Entity("db-1", "PENDING"), Entity("db-2", "PENDING")], NOW)
    repo.upsert_entities(tid, [Entity("db-1", "RESOLVED")], NOW)
    assert repo.list_entities(tid) == [Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")]


def test_notices_cursor_and_audit(repo):
    repo.put_notice(Notice("g1", "C1", "1.0", "C1"))
    repo.put_notice(Notice("g1", "C2", "2.0", "C0DEFAULT"))
    assert repo.get_notices("g1") == [Notice("g1", "C1", "1.0", "C1"), Notice("g1", "C2", "2.0", "C0DEFAULT")]
    assert repo.get_cursor("event") is None
    repo.set_cursor("event", NOW)
    assert repo.get_cursor("event") == NOW
    entry = AuditEntry(NOW, "U1", "ASSIGN", "OPEN", "OPEN", "U2")
    repo.append_audit(f"g1#{ACCOUNT}", entry)
    assert repo.list_audit(f"g1#{ACCOUNT}") == [entry]
