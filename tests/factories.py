from datetime import UTC, date, datetime

from aha.config import Config
from aha.domain.models import DueDateSource, Entity, EventSnapshot, State, Tier, TrackingItem

NOW = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)  # 2026-10-12 09:00 KST
TODAY = date(2026, 10, 12)
ARN = "arn:aws:health:ap-northeast-2::event/RDS/AWS_RDS_PLANNED_LIFECYCLE_EVENT/abc123"
ACCOUNT = "111122223333"
OTHER_ACCOUNT = "444455556666"


def make_config(**overrides) -> Config:
    return Config.from_dict({"default_channel": "C0DEFAULT", **overrides})


def make_snapshot(**overrides) -> EventSnapshot:
    base = dict(
        event_arn=ARN,
        account_id=ACCOUNT,
        service="RDS",
        event_type_code="AWS_RDS_PLANNED_LIFECYCLE_EVENT",
        category="scheduledChange",
        event_region="ap-northeast-2",
        status_code="upcoming",
        start_time=datetime(2026, 11, 30, 0, 0, tzinfo=UTC),
        end_time=None,
        last_updated_time=datetime(2026, 10, 1, tzinfo=UTC),
        actionability="ACTION_REQUIRED",
        personas=("OPERATIONS",),
        description="Amazon RDS for MySQL 5.7 end of standard support",
        entities=(Entity("db-1", "PENDING"), Entity("db-2", "PENDING")),
        source="RECONCILE",
    )
    base.update(overrides)
    return EventSnapshot(**base)


def make_item(**overrides) -> TrackingItem:
    base = dict(
        group_id="g1",
        account_id=ACCOUNT,
        tier=Tier.REQUIRED,
        state=State.OPEN,
        due_date=date(2026, 11, 30),
        due_date_source=DueDateSource.AWS,
        created_at=NOW,
    )
    base.update(overrides)
    return TrackingItem(**base)
