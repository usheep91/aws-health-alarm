from datetime import UTC, date, datetime, timedelta

from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Entity, IntentKind, State, Tier, tracking_id_of
from aha.domain.observe import observe, observe_with_retry
from aha.domain.repository import ConcurrentModification
from tests.factories import ACCOUNT, ARN, NOW, OTHER_ACCOUNT, make_config, make_snapshot
from tests.fakes import InMemoryRepository

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
LATER = datetime(2026, 10, 5, tzinfo=UTC)
RESOLVED_BOTH = (Entity("db-1", "RESOLVED"), Entity("db-2", "RESOLVED"))


def _kinds(intents):
    return [i.kind for i in intents]


def _initial(repo, **overrides):
    return observe(make_snapshot(**overrides), repo, CFG, NOW)


# --- 생성 경로 ---

def test_action_required_creates_open_item_and_new_intent():
    repo = InMemoryRepository()
    intents = _initial(repo)
    assert _kinds(intents) == [IntentKind.NEW]
    assert intents[0].tracking_id == TID
    item = repo.get_tracking(TID)
    assert item.state is State.OPEN
    assert item.tier is Tier.REQUIRED
    assert (item.due_date, item.due_date_source) == (date(2026, 11, 30), DueDateSource.AWS)
    assert item.aws_due_date == date(2026, 11, 30)
    assert item.counts.pending == 2
    assert item.manual_completion_required is False
    assert repo.list_audit(TID)[0].action == "CREATED"


def test_action_may_be_required_creates_triage_item():
    repo = InMemoryRepository()
    _initial(repo, actionability="ACTION_MAY_BE_REQUIRED")
    item = repo.get_tracking(TID)
    assert (item.state, item.tier) == (State.TRIAGE, Tier.REVIEW)


def test_informational_is_ignored():
    repo = InMemoryRepository()
    assert _initial(repo, actionability="INFORMATIONAL") == []
    assert repo.tracking == {}


def test_event_without_dynamic_status_requires_manual_completion():
    repo = InMemoryRepository()
    _initial(repo, entities=(Entity("i-1", "UNKNOWN"),))
    assert repo.get_tracking(TID).manual_completion_required is True


def test_already_resolved_on_discovery_is_stored_silently():
    repo = InMemoryRepository()
    assert _initial(repo, entities=RESOLVED_BOTH) == []
    assert repo.get_tracking(TID).state is State.RESOLVED_AUTO


def test_reobserving_same_snapshot_is_silent():
    repo = InMemoryRepository()
    _initial(repo)
    version = repo.get_tracking(TID).version
    assert _initial(repo) == []
    assert repo.get_tracking(TID).version == version


def test_second_account_joins_same_group():
    repo = InMemoryRepository()
    _initial(repo)
    intents = _initial(repo, account_id=OTHER_ACCOUNT)
    assert _kinds(intents) == [IntentKind.NEW]
    assert [i.account_id for i in repo.list_group_tracking(GID)] == [ACCOUNT, OTHER_ACCOUNT]


def test_split_event_arn_links_to_existing_item():
    repo = InMemoryRepository()
    _initial(repo)
    part2 = ARN + "-part2"
    intents = _initial(repo, event_arn=part2, entities=(Entity("db-3", "PENDING"),))
    assert _kinds(intents) == [IntentKind.PROGRESS]
    assert repo.get_tracking(TID).event_arns == {ARN, part2}
    assert repo.get_group(GID).event_arns == {ARN, part2}


# --- 갱신 경로 ---

def test_aws_due_date_change_is_applied():
    repo = InMemoryRepository()
    _initial(repo)
    intents = _initial(repo, start_time=datetime(2027, 2, 28, tzinfo=UTC), last_updated_time=LATER)
    assert _kinds(intents) == [IntentKind.DUE_CHANGED]
    assert intents[0].data == {"old": "2026-11-30", "new": "2027-02-28", "source": "AWS", "kept_manual": False}
    assert repo.get_tracking(TID).due_date == date(2027, 2, 28)


def test_aws_due_date_change_resets_reminders():
    repo = InMemoryRepository()
    _initial(repo)
    item = repo.get_tracking(TID)
    item.reminders_sent = {"BASELINE", "D-90"}
    repo.save_tracking(item)
    _initial(repo, start_time=datetime(2027, 2, 28, tzinfo=UTC), last_updated_time=LATER)
    assert repo.get_tracking(TID).reminders_sent == set()


def test_unchanged_due_date_keeps_reminders():
    repo = InMemoryRepository()
    _initial(repo)
    item = repo.get_tracking(TID)
    item.reminders_sent = {"BASELINE", "D-90"}
    repo.save_tracking(item)
    _initial(repo, last_updated_time=LATER)
    assert repo.get_tracking(TID).reminders_sent == {"BASELINE", "D-90"}


def test_manual_due_date_is_kept_when_aws_changes():
    repo = InMemoryRepository()
    _initial(repo)
    item = repo.get_tracking(TID)
    item.due_date, item.due_date_source = date(2026, 12, 15), DueDateSource.MANUAL
    repo.save_tracking(item)
    intents = _initial(repo, start_time=datetime(2027, 2, 28, tzinfo=UTC), last_updated_time=LATER)
    assert intents[0].data == {"old": "2026-11-30", "new": "2027-02-28", "source": "AWS", "kept_manual": True}
    item = repo.get_tracking(TID)
    assert (item.due_date, item.aws_due_date) == (date(2026, 12, 15), date(2027, 2, 28))


def test_progress_when_entity_resolved():
    repo = InMemoryRepository()
    _initial(repo)
    intents = _initial(repo, entities=(Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")))
    assert _kinds(intents) == [IntentKind.PROGRESS]
    assert intents[0].data == {"old_pending": 2, "new_pending": 1, "total": 2}


def test_all_entities_resolved_closes_item():
    repo = InMemoryRepository()
    _initial(repo)
    later = NOW + timedelta(hours=6)
    intents = observe(make_snapshot(entities=RESOLVED_BOTH), repo, CFG, later)
    assert _kinds(intents) == [IntentKind.RESOLVED]
    item = repo.get_tracking(TID)
    assert (item.state, item.resolved_at) == (State.RESOLVED_AUTO, later)


def test_new_pending_entity_reopens_resolved_item():
    repo = InMemoryRepository()
    _initial(repo)
    _initial(repo, entities=RESOLVED_BOTH)
    intents = _initial(repo, entities=(Entity("db-3", "PENDING"),))
    assert _kinds(intents) == [IntentKind.REOPENED]
    item = repo.get_tracking(TID)
    assert (item.state, item.resolved_at) == (State.OPEN, None)


def test_aws_closed_event_without_dynamic_status():
    repo = InMemoryRepository()
    _initial(repo, entities=(Entity("i-1", "UNKNOWN"),))
    intents = _initial(repo, entities=(Entity("i-1", "UNKNOWN"),), status_code="closed", last_updated_time=LATER)
    assert _kinds(intents) == [IntentKind.CLOSED_BY_AWS]


def test_description_change_emits_single_group_intent():
    repo = InMemoryRepository()
    _initial(repo)
    _initial(repo, account_id=OTHER_ACCOUNT)
    first = _initial(repo, description="updated", last_updated_time=LATER)
    second = _initial(repo, account_id=OTHER_ACCOUNT, description="updated", last_updated_time=LATER)
    assert _kinds(first) == [IntentKind.CONTENT_UPDATED]
    assert first[0].tracking_id is None
    assert second == []


def test_stale_snapshot_does_not_overwrite_event():
    repo = InMemoryRepository()
    _initial(repo, description="new", last_updated_time=LATER)
    _initial(repo, description="old", last_updated_time=datetime(2026, 10, 1, tzinfo=UTC))
    assert repo.get_event(ARN).description == "new"


def test_review_item_promoted_when_action_required():
    repo = InMemoryRepository()
    _initial(repo, actionability="ACTION_MAY_BE_REQUIRED")
    intents = _initial(repo)
    assert _kinds(intents) == [IntentKind.PROMOTED]
    item = repo.get_tracking(TID)
    assert (item.state, item.tier) == (State.OPEN, Tier.REQUIRED)


def test_manual_resolution_mismatch_flagged_once_after_grace():
    repo = InMemoryRepository()
    _initial(repo)
    item = repo.get_tracking(TID)
    item.state, item.manual_resolved_at, item.resolved_at = State.RESOLVED_MANUAL, NOW, NOW
    repo.save_tracking(item)
    assert observe(make_snapshot(), repo, CFG, NOW + timedelta(hours=71)) == []
    intents = observe(make_snapshot(), repo, CFG, NOW + timedelta(hours=73))
    assert _kinds(intents) == [IntentKind.MISMATCH]
    assert intents[0].data == {"pending": 2}
    assert observe(make_snapshot(), repo, CFG, NOW + timedelta(hours=74)) == []


class FlakyRepository(InMemoryRepository):
    def __init__(self):
        super().__init__()
        self.fail_next_save = False

    def save_tracking(self, item):
        if self.fail_next_save:
            self.fail_next_save = False
            raise ConcurrentModification(item.tracking_id)
        super().save_tracking(item)


def test_observe_with_retry_recovers_from_conflict():
    repo = FlakyRepository()
    _initial(repo)
    repo.fail_next_save = True
    intents = observe_with_retry(make_snapshot(entities=RESOLVED_BOTH), repo, CFG, NOW)
    assert _kinds(intents) == [IntentKind.RESOLVED]


def test_retry_preserves_content_updated():
    repo = FlakyRepository()
    _initial(repo)
    repo.fail_next_save = True
    intents = observe_with_retry(
        make_snapshot(
            description="updated",
            last_updated_time=LATER,
            entities=(Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")),
        ),
        repo,
        CFG,
        NOW,
    )
    assert _kinds(intents) == [IntentKind.CONTENT_UPDATED, IntentKind.PROGRESS]
    assert repo.get_event(ARN).description == "updated"


def test_stale_snapshot_does_not_change_tracking():
    repo = InMemoryRepository()
    _initial(repo)
    _initial(repo, start_time=datetime(2027, 2, 28, tzinfo=UTC), last_updated_time=LATER)
    stale = _initial(repo, last_updated_time=datetime(2026, 10, 1, tzinfo=UTC))
    assert stale == []
    assert repo.get_tracking(TID).due_date == date(2027, 2, 28)
