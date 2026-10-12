import pytest

from aha.domain.classify import classify
from aha.domain.models import Entity, Tier
from tests.factories import make_config, make_snapshot

CFG = make_config()


@pytest.mark.parametrize(
    ("actionability", "expected"),
    [
        ("ACTION_REQUIRED", Tier.REQUIRED),
        ("ACTION_MAY_BE_REQUIRED", Tier.REVIEW),
        ("INFORMATIONAL", Tier.EXCLUDED),
    ],
)
def test_actionability_is_primary_signal(actionability, expected):
    assert classify(make_snapshot(actionability=actionability), CFG) == (expected, "ACTIONABILITY")


def test_suppress_list_applies_only_to_review_tier():
    cfg = make_config(suppress_event_types=["AWS_RDS_PLANNED_LIFECYCLE_EVENT"])
    assert classify(make_snapshot(actionability="ACTION_MAY_BE_REQUIRED"), cfg) == (Tier.EXCLUDED, "SUPPRESSED")
    assert classify(make_snapshot(actionability="ACTION_REQUIRED"), cfg) == (Tier.REQUIRED, "ACTIONABILITY")


def test_force_list_overrides_informational():
    cfg = make_config(force_track_event_types=["AWS_RDS_PLANNED_LIFECYCLE_EVENT"])
    assert classify(make_snapshot(actionability="INFORMATIONAL"), cfg) == (Tier.REQUIRED, "FORCED")


@pytest.mark.parametrize(
    ("category", "code", "entities", "expected"),
    [
        ("scheduledChange", "AWS_EC2_MAINTENANCE", (), Tier.REQUIRED),
        ("issue", "AWS_EC2_INSTANCE_RETIREMENT_SCHEDULED", (), Tier.REQUIRED),
        ("accountNotification", "AWS_RDS_PLANNED_LIFECYCLE_EVENT", (), Tier.REQUIRED),
        ("accountNotification", "AWS_IAM_NOTICE", (), Tier.REVIEW),
        ("issue", "AWS_EC2_OPERATIONAL_ISSUE", (Entity("i-1", "IMPAIRED"),), Tier.REVIEW),
        ("issue", "AWS_EC2_OPERATIONAL_ISSUE", (), Tier.EXCLUDED),
        ("investigation", "AWS_EC2_INVESTIGATION", (), Tier.EXCLUDED),
    ],
)
def test_fallback_when_actionability_missing(category, code, entities, expected):
    snap = make_snapshot(actionability=None, category=category, event_type_code=code, entities=entities)
    assert classify(snap, CFG) == (expected, "FALLBACK")
