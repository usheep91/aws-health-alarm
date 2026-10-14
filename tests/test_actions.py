from datetime import date

import pytest

from aha.domain.actions import ActionKind, InvalidAction, apply_group_action, auto_assign
from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Entity, IntentKind, State, Tier, tracking_id_of
from aha.domain.observe import observe
from aha.domain.repository import ConcurrentModification
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, make_config, make_item, make_snapshot
from tests.fakes import InMemoryRepository

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
OTHER_TID = tracking_id_of(GID, OTHER_ACCOUNT)


class ConflictingRepository(InMemoryRepository):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def save_tracking(self, item):
        if self.failures:
            self.failures -= 1
            raise ConcurrentModification(item.tracking_id)
        super().save_tracking(item)


class OtherAccountConflictingRepository(InMemoryRepository):
    def save_tracking(self, item):
        if item.account_id == OTHER_ACCOUNT:
            raise ConcurrentModification(item.tracking_id)
        super().save_tracking(item)


def _conflicting(failures):
    repo = ConflictingRepository(0)
    observe(make_snapshot(), repo, CFG, NOW)
    repo.failures = failures
    return repo


def _repo(**overrides):
    repo = InMemoryRepository()
    observe(make_snapshot(**overrides), repo, CFG, NOW)
    return repo


def _act(repo, kind, tids=(TID,), **kw):
    return apply_group_action(repo, list(tids), kind, "U1", NOW, channel="C1", **kw)


def test_assign_returns_single_action_intent():
    repo = _repo()
    intents = _act(repo, ActionKind.ASSIGN, assignee="U2")
    assert intents[0].kind is IntentKind.ACTION
    assert intents[0].tracking_id is None
    assert intents[0].data == {
        "action": "ASSIGN",
        "actor": "U1",
        "accounts": [ACCOUNT],
        "assignee": "U2",
        "due": None,
        "reason": None,
        "channel": "C1",
    }
    assert repo.get_tracking(TID).assignee == "U2"
    assert repo.list_audit(TID)[-1].action == "ASSIGN"
    assert repo.list_audit(TID)[-1].detail == "U2"


def test_set_due_marks_manual_and_resets_reminders():
    repo = _repo()
    item = repo.get_tracking(TID)
    item.reminders_sent = {"D-30"}
    repo.save_tracking(item)
    _act(repo, ActionKind.SET_DUE, due=date(2026, 12, 31), reason="벤더 일정")
    item = repo.get_tracking(TID)
    assert (item.due_date, item.due_date_source, item.reminders_sent) == (date(2026, 12, 31), DueDateSource.MANUAL, set())
    assert repo.list_audit(TID)[-1].detail == "2026-12-31 (벤더 일정)"


def test_resolve_manual():
    repo = _repo()
    _act(repo, ActionKind.RESOLVE_MANUAL)
    item = repo.get_tracking(TID)
    assert (item.state, item.resolved_at, item.manual_resolved_at) == (State.RESOLVED_MANUAL, NOW, NOW)


def test_not_applicable_requires_reason():
    repo = _repo()
    with pytest.raises(InvalidAction):
        _act(repo, ActionKind.NOT_APPLICABLE, reason="  ")
    _act(repo, ActionKind.NOT_APPLICABLE, reason="해당 DB는 이미 마이그레이션 완료")
    assert repo.get_tracking(TID).state is State.NOT_APPLICABLE


def test_triage_required_promotes_triage_item():
    repo = _repo(actionability="ACTION_MAY_BE_REQUIRED")
    _act(repo, ActionKind.TRIAGE_REQUIRED)
    item = repo.get_tracking(TID)
    assert (item.state, item.tier) == (State.OPEN, Tier.REQUIRED)


def test_single_target_errors_propagate():
    repo = _repo()
    with pytest.raises(InvalidAction):
        _act(repo, ActionKind.TRIAGE_REQUIRED)
    _act(repo, ActionKind.RESOLVE_MANUAL)
    with pytest.raises(InvalidAction):
        _act(repo, ActionKind.RESOLVE_MANUAL)


def test_group_action_skips_terminal_items():
    repo = _repo()
    observe(make_snapshot(account_id=OTHER_ACCOUNT, entities=(Entity("db-9", "RESOLVED"),)), repo, CFG, NOW)
    intents = _act(repo, ActionKind.ASSIGN, tids=(TID, OTHER_TID), assignee="U2")
    assert intents[0].data["accounts"] == [ACCOUNT]
    assert repo.get_tracking(OTHER_TID).assignee is None


def test_retry_succeeds_after_conflicts():
    repo = _conflicting(2)
    _act(repo, ActionKind.ASSIGN, assignee="U2")
    assert repo.get_tracking(TID).assignee == "U2"
    assert [e.action for e in repo.list_audit(TID)].count("ASSIGN") == 1


def test_retry_gives_up_after_three_conflicts_for_single_target():
    repo = _conflicting(3)
    with pytest.raises(ConcurrentModification):
        _act(repo, ActionKind.ASSIGN, assignee="U2")


def test_group_skips_item_whose_retries_are_exhausted():
    repo = OtherAccountConflictingRepository()
    observe(make_snapshot(), repo, CFG, NOW)
    observe(make_snapshot(account_id=OTHER_ACCOUNT), repo, CFG, NOW)
    intents = _act(repo, ActionKind.ASSIGN, tids=(TID, OTHER_TID), assignee="U2")
    assert intents[0].data["accounts"] == [ACCOUNT]
    assert repo.get_tracking(TID).assignee == "U2"
    assert repo.get_tracking(OTHER_TID).assignee is None


# --- 자동 담당자 지정 ---


def _auto_repo(*items):
    repo = InMemoryRepository()
    for item in items:
        repo.create_tracking(item)
    return repo


def test_auto_assign_sets_assignee_and_emits_one_intent_per_group():
    repo = _auto_repo(
        make_item(group_id="g1", account_id=ACCOUNT),
        make_item(group_id="g1", account_id=OTHER_ACCOUNT),
        make_item(group_id="g2", account_id=ACCOUNT, state=State.TRIAGE, tier=Tier.REVIEW),
    )
    intents = auto_assign(repo, {f"g1#{ACCOUNT}": "U1", f"g1#{OTHER_ACCOUNT}": "U2", f"g2#{ACCOUNT}": "U1"}, NOW)
    assert [(i.kind, i.group_id, i.tracking_id) for i in intents] == [
        (IntentKind.ACTION, "g1", None),
        (IntentKind.ACTION, "g2", None),
    ]
    assert intents[0].data == {
        "action": "AUTO_ASSIGN",
        "actor": "system",
        "assignments": {ACCOUNT: "U1", OTHER_ACCOUNT: "U2"},
        "accounts": [ACCOUNT, OTHER_ACCOUNT],
        "channel": None,
    }
    assert repo.get_tracking(f"g1#{OTHER_ACCOUNT}").assignee == "U2"
    assert repo.get_tracking(f"g2#{ACCOUNT}").state is State.TRIAGE
    audit = repo.list_audit(f"g1#{ACCOUNT}")[-1]
    assert (audit.actor, audit.action, audit.before, audit.after, audit.detail) == ("system", "AUTO_ASSIGN", "OPEN", "OPEN", "U1")


def test_auto_assign_never_overwrites_existing_assignee():
    repo = _auto_repo(make_item(assignee="UMANUAL"))
    assert auto_assign(repo, {f"g1#{ACCOUNT}": "U1"}, NOW) == []
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee == "UMANUAL"
    assert repo.list_audit(f"g1#{ACCOUNT}") == []


def test_auto_assign_skips_missing_and_terminal_items():
    repo = _auto_repo(make_item(state=State.RESOLVED_AUTO))
    assert auto_assign(repo, {f"g1#{ACCOUNT}": "U1", "g9#999999999999": "U2"}, NOW) == []
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee is None


def test_auto_assign_retries_conflicts():
    repo = ConflictingRepository(0)
    repo.create_tracking(make_item())
    repo.failures = 2
    intents = auto_assign(repo, {f"g1#{ACCOUNT}": "U1"}, NOW)
    assert intents[0].data["assignments"] == {ACCOUNT: "U1"}
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee == "U1"
    assert [e.action for e in repo.list_audit(f"g1#{ACCOUNT}")] == ["AUTO_ASSIGN"]


def test_auto_assign_skips_item_whose_retries_are_exhausted():
    repo = ConflictingRepository(0)
    repo.create_tracking(make_item())
    repo.failures = 3
    assert auto_assign(repo, {f"g1#{ACCOUNT}": "U1"}, NOW) == []
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee is None
