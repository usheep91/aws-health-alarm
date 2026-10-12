from datetime import timedelta

import pytest

from aha.domain.models import EntityCounts, State, Tier
from aha.domain.state_machine import needs_mismatch_flag, next_state
from tests.factories import NOW, make_item

ALL_RESOLVED = EntityCounts(pending=0, resolved=2, total=2)
ONE_PENDING = EntityCounts(pending=1, resolved=1, total=2)


@pytest.mark.parametrize("state", [State.OPEN, State.TRIAGE])
def test_active_item_auto_resolves_when_all_entities_resolved(state):
    assert next_state(make_item(state=state), ALL_RESOLVED, True, "open") is State.RESOLVED_AUTO


def test_no_auto_resolve_without_entities():
    assert next_state(make_item(), EntityCounts(), True, "open") is State.OPEN


def test_closed_event_without_dynamic_status_closes_by_aws():
    assert next_state(make_item(), EntityCounts(total=1), False, "closed") is State.CLOSED_BY_AWS


def test_closed_dynamic_event_with_pending_stays_open():
    assert next_state(make_item(), ONE_PENDING, True, "closed") is State.OPEN


def test_resolved_auto_reopens_on_new_pending():
    assert next_state(make_item(state=State.RESOLVED_AUTO), ONE_PENDING, True, "open") is State.OPEN
    review = make_item(state=State.RESOLVED_AUTO, tier=Tier.REVIEW)
    assert next_state(review, ONE_PENDING, True, "open") is State.TRIAGE


@pytest.mark.parametrize("state", [State.RESOLVED_MANUAL, State.NOT_APPLICABLE, State.CLOSED_BY_AWS])
def test_other_terminal_states_are_sticky(state):
    assert next_state(make_item(state=state), ONE_PENDING, True, "open") is state


def test_mismatch_flag_after_grace_period():
    item = make_item(state=State.RESOLVED_MANUAL, manual_resolved_at=NOW)
    assert not needs_mismatch_flag(item, ONE_PENDING, NOW + timedelta(hours=71))
    assert needs_mismatch_flag(item, ONE_PENDING, NOW + timedelta(hours=72))
    assert not needs_mismatch_flag(item, ALL_RESOLVED, NOW + timedelta(hours=72))
    item.verification_mismatch = True
    assert not needs_mismatch_flag(item, ONE_PENDING, NOW + timedelta(hours=72))
