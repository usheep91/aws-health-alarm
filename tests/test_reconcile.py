from datetime import UTC, datetime, timedelta

from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import Entity, IntentKind, Notice, State, tracking_id_of
from aha.reconcile import CURSOR_OVERLAP, ReconcileResult, Reconciler
from aha.routing import StaticRouter
from tests.factories import ACCOUNT, ARN, NOW, make_config, make_snapshot
from tests.fakes import FakeHealth, InMemoryRepository, RecordingPublisher

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
EVENT = {
    "arn": ARN,
    "service": "RDS",
    "eventTypeCode": "AWS_RDS_PLANNED_LIFECYCLE_EVENT",
    "eventTypeCategory": "scheduledChange",
    "region": "ap-northeast-2",
    "eventScopeCode": "ACCOUNT_SPECIFIC",
    "statusCode": "upcoming",
    "startTime": datetime(2026, 11, 30, tzinfo=UTC),
    "lastUpdatedTime": datetime(2026, 10, 1, tzinfo=UTC),
    "actionability": "ACTION_REQUIRED",
    "personas": ["OPERATIONS"],
}


def _health(events=(EVENT,)):
    return FakeHealth(list(events), {ARN: [ACCOUNT]}, {(ARN, ACCOUNT): [Entity("db-1", "PENDING")]}, {ARN: "desc"})


def _reconciler(health):
    repo, pub = InMemoryRepository(), RecordingPublisher()
    return Reconciler(health, repo, pub, StaticRouter("C0SHADOW"), CFG), repo, pub


def test_first_event_sweep_uses_open_upcoming_and_sets_cursor():
    health = _health()
    rec, repo, pub = _reconciler(health)
    result = rec.run("event", NOW)
    assert health.calls == [(None, ("open", "upcoming"))]
    assert result == ReconcileResult(events=1, observations=1, created=1)
    assert [i.kind for i in pub.intents] == [IntentKind.NEW]
    assert repo.get_event(ARN).description == "desc"
    assert repo.get_cursor("event") == NOW


def test_next_event_sweep_overlaps_cursor_and_includes_closed():
    health = _health()
    rec, repo, _ = _reconciler(health)
    repo.set_cursor("event", NOW)
    rec.run("event", NOW + timedelta(hours=1))
    assert health.calls == [(NOW - CURSOR_OVERLAP, ("open", "upcoming", "closed"))]


def test_skips_informational_and_public_events():
    events = [{**EVENT, "arn": "a-info", "actionability": "INFORMATIONAL"}, {**EVENT, "arn": "a-pub", "eventScopeCode": "PUBLIC"}]
    rec, _, pub = _reconciler(_health(events))
    assert rec.run("event", NOW).events == 0
    assert pub.intents == []


def test_full_resync_counts_created_and_keeps_cursor():
    health = _health()
    rec, repo, _ = _reconciler(health)
    result = rec.run("full", NOW)
    assert health.calls == [(None, ("open", "upcoming"))]
    assert result.created == 1
    assert repo.get_cursor("event") is None


def test_entity_sweep_resolves_items():
    health = _health()
    rec, repo, pub = _reconciler(health)
    rec.run("event", NOW)
    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0SHADOW"))
    health.entities[(ARN, ACCOUNT)] = [Entity("db-1", "RESOLVED")]
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(hours=6))
    assert [i.kind for i in pub.intents] == [IntentKind.RESOLVED]


def test_entity_sweep_republishes_missing_notice():
    rec, repo, pub = _reconciler(_health())
    rec.run("event", NOW)
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(hours=6))
    assert [(i.kind, i.group_id) for i in pub.intents] == [(IntentKind.REFRESH, GID)]

    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0SHADOW"))
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(hours=12))
    assert pub.intents == []


def test_entity_sweep_reopens_recently_resolved_item():
    health = _health()
    rec, repo, pub = _reconciler(health)
    rec.run("event", NOW)
    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0SHADOW"))
    health.entities[(ARN, ACCOUNT)] = [Entity("db-1", "RESOLVED")]
    rec.run("entity", NOW + timedelta(hours=6))
    health.entities[(ARN, ACCOUNT)] = [Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")]
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(days=10))
    assert [i.kind for i in pub.intents] == [IntentKind.REOPENED]


def test_entity_sweep_stops_checking_old_resolved_items():
    health = _health()
    rec, repo, pub = _reconciler(health)
    rec.run("event", NOW)
    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0SHADOW"))
    health.entities[(ARN, ACCOUNT)] = [Entity("db-1", "RESOLVED")]
    rec.run("entity", NOW + timedelta(hours=6))
    health.entities[(ARN, ACCOUNT)] = [Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")]
    pub.intents.clear()
    result = rec.run("entity", NOW + timedelta(days=31))
    assert result.observations == 0
    assert pub.intents == []


def test_entity_sweep_stops_checking_manual_resolution_after_watch_window():
    health = _health()
    rec, repo, pub = _reconciler(health)
    rec.run("event", NOW)
    item = repo.get_tracking(tracking_id_of(GID, ACCOUNT))
    item.state, item.resolved_at, item.manual_resolved_at = State.RESOLVED_MANUAL, NOW, NOW
    repo.save_tracking(item)
    assert rec.run("entity", NOW + timedelta(hours=80)).observations == 1
    assert rec.run("entity", NOW + timedelta(hours=85)).observations == 0
