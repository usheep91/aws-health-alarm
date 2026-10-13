import json
from datetime import date

from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Notice, State, tracking_id_of
from aha.domain.observe import observe
from aha.routing import StaticRouter
from aha.slack.interactions import InteractionService
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, make_config, make_snapshot
from tests.fakes import FakeSlack, InMemoryRepository, RecordingPublisher

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
OTHER_TID = tracking_id_of(GID, OTHER_ACCOUNT)


def _setup(accounts=(ACCOUNT,), **overrides):
    repo = InMemoryRepository()
    for account in accounts:
        observe(make_snapshot(account_id=account, **overrides), repo, CFG, NOW)
    slack, pub = FakeSlack(), RecordingPublisher()
    service = InteractionService(repo, slack, StaticRouter("C0SHADOW"), pub, CFG, clock=lambda: NOW)
    return repo, slack, pub, service


def _block_action(action, channel="C0SHADOW"):
    return {"type": "block_actions", "user": {"id": "U1"}, "channel": {"id": channel}, "trigger_id": "T1", "actions": [action]}


def _group_action(action_id, **extra):
    return _block_action({"action_id": action_id, "block_id": f"grp:{GID}", **extra})


def _menu(value):
    return _block_action({"action_id": "account_menu", "block_id": "b1", "selected_option": {"value": value}})


def _submission(callback_id, meta, values):
    return {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {"callback_id": callback_id, "private_metadata": json.dumps(meta), "state": {"values": values}},
    }


def test_overflow_resolve_marks_manual_resolution():
    repo, _, pub, service = _setup()
    assert service.handle(_menu(f"resolve:{TID}")) is None
    assert repo.get_tracking(TID).state is State.RESOLVED_MANUAL
    assert (pub.intents[0].data["action"], pub.intents[0].data["channel"]) == ("RESOLVE_MANUAL", "C0SHADOW")


def test_overflow_na_opens_reason_modal():
    _, slack, _, service = _setup()
    service.handle(_menu(f"na:{TID}"))
    view = slack.views[0]["view"]
    assert view["callback_id"] == "na_modal"
    assert json.loads(view["private_metadata"]) == {"tracking_ids": [TID], "channel": "C0SHADOW"}


def test_assign_applies_to_active_items_in_channel():
    repo, _, pub, service = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    service.handle(_group_action("assign", selected_user="U2"))
    assert repo.get_tracking(TID).assignee == repo.get_tracking(OTHER_TID).assignee == "U2"
    assert len(pub.intents) == 1
    assert pub.intents[0].data["accounts"] == [ACCOUNT, OTHER_ACCOUNT]


def test_set_due_opens_modal_with_earliest_due():
    _, slack, _, service = _setup()
    service.handle(_group_action("set_due"))
    view = slack.views[0]["view"]
    assert view["callback_id"] == "set_due_modal"
    assert view["blocks"][0]["element"]["initial_date"] == "2026-11-30"


def _due_values(day, reason="일정 조정"):
    return {"due": {"value": {"selected_date": day}}, "reason": {"value": {"value": reason}}}


def test_set_due_submission_rejects_past_date():
    _, _, _, service = _setup()
    result = service.handle(_submission("set_due_modal", {"tracking_ids": [TID], "channel": "C0SHADOW"}, _due_values("2026-10-01")))
    assert result == {"response_action": "errors", "errors": {"due": "오늘 이후 날짜를 선택하세요"}}


def test_set_due_submission_saves_manual_date():
    repo, _, _, service = _setup()
    result = service.handle(_submission("set_due_modal", {"tracking_ids": [TID], "channel": "C0SHADOW"}, _due_values("2026-12-31")))
    assert result == {"response_action": "clear"}
    item = repo.get_tracking(TID)
    assert (item.due_date, item.due_date_source) == (date(2026, 12, 31), DueDateSource.MANUAL)


def test_na_submission_with_blank_reason_returns_error():
    _, _, _, service = _setup()
    values = {"reason": {"value": {"value": "   "}}}
    result = service.handle(_submission("na_modal", {"tracking_ids": [TID], "channel": "C0SHADOW"}, values))
    assert result == {"response_action": "errors", "errors": {"reason": "사유를 입력하세요"}}


def test_triage_required_promotes_triage_items():
    repo, _, _, service = _setup(actionability="ACTION_MAY_BE_REQUIRED")
    service.handle(_group_action("triage_required"))
    assert repo.get_tracking(TID).state is State.OPEN


def test_resources_opens_modal():
    _, slack, _, service = _setup()
    service.handle(_group_action("resources"))
    assert slack.views[0]["view"]["title"]["text"] == "영향 리소스"


def test_action_from_fallback_channel_maps_to_logical_channel():
    repo, _, pub, service = _setup()
    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0DEFAULT"))
    service.handle(_block_action({"action_id": "assign", "block_id": f"grp:{GID}", "selected_user": "U2"}, channel="C0DEFAULT"))
    assert repo.get_tracking(TID).assignee == "U2"
    assert pub.intents[0].data["channel"] == "C0SHADOW"


def test_logical_channel_prefers_clicked_message():
    repo, _, pub, service = _setup()
    repo.put_notice(Notice(GID, "C0DEFAULT", "1.0", "C0DEFAULT"))
    repo.put_notice(Notice(GID, "C0SHADOW", "2.0", "C0DEFAULT"))
    payload = _block_action({"action_id": "assign", "block_id": f"grp:{GID}", "selected_user": "U2"}, channel="C0DEFAULT")
    payload["container"] = {"message_ts": "2.0"}
    service.handle(payload)
    assert repo.get_tracking(TID).assignee == "U2"
    assert pub.intents[0].data["channel"] == "C0SHADOW"


def test_set_due_modal_metadata_is_compact():
    _, slack, _, service = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    service.handle(_group_action("set_due"))
    meta = json.loads(slack.views[0]["view"]["private_metadata"])
    assert meta == {"group_id": GID, "channel": "C0SHADOW", "scope": "active"}


def test_group_scoped_submission_applies_to_active_items():
    repo, _, _, service = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    meta = {"group_id": GID, "channel": "C0SHADOW", "scope": "active"}
    service.handle(_submission("set_due_modal", meta, _due_values("2026-12-31")))
    assert repo.get_tracking(TID).due_date == repo.get_tracking(OTHER_TID).due_date == date(2026, 12, 31)


def test_triage_na_submission_applies_only_to_triage_items():
    repo, slack, _, service = _setup(actionability="ACTION_MAY_BE_REQUIRED")
    service.handle(_group_action("triage_na"))
    meta = json.loads(slack.views[0]["view"]["private_metadata"])
    assert meta["scope"] == "triage"
    service.handle(_submission("na_modal", meta, {"reason": {"value": {"value": "대상 아님"}}}))
    assert repo.get_tracking(TID).state is State.NOT_APPLICABLE


def test_set_due_submission_requires_reason():
    _, _, _, service = _setup()
    meta = {"tracking_ids": [TID], "channel": "C0SHADOW"}
    result = service.handle(_submission("set_due_modal", meta, _due_values("2026-12-31", reason="  ")))
    assert result == {"response_action": "errors", "errors": {"reason": "변경 사유를 입력하세요"}}


def test_resources_modal_contains_export_button():
    _, slack, _, service = _setup()
    service.handle(_group_action("resources"))
    first = slack.views[0]["view"]["blocks"][0]
    assert first["block_id"] == "resources_export"
    assert json.loads(first["elements"][0]["value"]) == {"group_id": GID, "channel": "C0SHADOW"}


def test_export_click_from_modal_publishes_export_intent():
    from aha.domain.models import Intent, IntentKind

    _, slack, pub, service = _setup()
    payload = {
        "type": "block_actions",
        "user": {"id": "U9"},
        "trigger_id": "T1",
        "view": {"id": "V1"},
        "actions": [
            {
                "action_id": "export_resources",
                "block_id": "resources_export",
                "value": json.dumps({"group_id": GID, "channel": "C0SHADOW"}),
            }
        ],
    }
    assert service.handle(payload) is None
    assert pub.intents == [Intent(IntentKind.EXPORT, GID, None, {"channel": "C0SHADOW", "requester": "U9"})]
    assert slack.views == []
