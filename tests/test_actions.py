from datetime import date

import pytest

from aha.domain.actions import ActionKind, InvalidAction, apply_group_action
from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Entity, IntentKind, State, Tier, tracking_id_of
from aha.domain.observe import observe
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, make_config, make_snapshot
from tests.fakes import InMemoryRepository

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
OTHER_TID = tracking_id_of(GID, OTHER_ACCOUNT)


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


def test_set_due_marks_manual_and_resets_reminders():
    repo = _repo()
    item = repo.get_tracking(TID)
    item.reminders_sent = {"D-30"}
    repo.save_tracking(item)
    _act(repo, ActionKind.SET_DUE, due=date(2026, 12, 31), reason="벤더 일정")
    item = repo.get_tracking(TID)
    assert (item.due_date, item.due_date_source, item.reminders_sent) == (date(2026, 12, 31), DueDateSource.MANUAL, set())


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
