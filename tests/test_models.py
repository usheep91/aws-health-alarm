from aha.domain.models import (
    Entity,
    EntityCounts,
    Intent,
    IntentKind,
    State,
    split_tracking_id,
    tracking_id_of,
)
from tests.factories import make_item


def test_entity_counts():
    counts = EntityCounts.of([Entity("a", "PENDING"), Entity("b", "RESOLVED"), Entity("c", "UNKNOWN")])
    assert counts == EntityCounts(pending=1, resolved=1, total=3)


def test_tracking_id_roundtrip():
    tid = tracking_id_of("abcd", "111122223333")
    assert tid == "abcd#111122223333"
    assert split_tracking_id(tid) == ("abcd", "111122223333")


def test_tracking_item_terminal():
    assert not make_item(state=State.OPEN).is_terminal
    assert make_item(state=State.RESOLVED_AUTO).is_terminal


def test_intent_json_roundtrip():
    intent = Intent(IntentKind.DUE_CHANGED, "g1", "g1#111122223333", {"old": "2026-11-30", "new": "2027-02-28"})
    assert Intent.from_json(intent.to_json()) == intent
