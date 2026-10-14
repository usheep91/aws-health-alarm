from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import Entity, Intent, IntentKind
from aha.domain.observe import observe
from aha.notifier import Notifier
from aha.routing import StaticRouter
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, TODAY, make_config, make_snapshot
from tests.fakes import FakeSlack, InMemoryRepository, MapRouter

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))


def _setup(accounts=(ACCOUNT,), router=None, slack=None, **overrides):
    repo = InMemoryRepository()
    intents = [i for a in accounts for i in observe(make_snapshot(account_id=a, **overrides), repo, CFG, NOW)]
    slack = slack or FakeSlack()
    notifier = Notifier(repo, slack, router or StaticRouter("C0SHADOW"), CFG)
    return repo, slack, notifier, intents


def test_new_intent_posts_notice_once():
    repo, slack, notifier, intents = _setup()
    notifier.handle(intents[0], TODAY)
    assert len(slack.posts) == 1
    assert (slack.posts[0]["channel"], slack.posts[0]["thread_ts"]) == ("C0SHADOW", None)
    notice = repo.get_notices(GID)[0]
    assert (notice.channel_id, notice.ts, notice.posted_channel_id) == ("C0SHADOW", slack.posts[0]["ts"], "C0SHADOW")


def test_second_account_updates_notice_without_thread():
    _, slack, notifier, intents = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    notifier.handle(intents[0], TODAY)
    notifier.handle(intents[1], TODAY)
    assert len(slack.updates) == 1
    assert len(slack.posts) == 1
    assert slack.posts[0]["thread_ts"] is None


def test_intents_are_scoped_to_their_channel():
    router = MapRouter({ACCOUNT: "C1", OTHER_ACCOUNT: "C2"})
    _, slack, notifier, intents = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT), router=router)
    for intent in intents:
        notifier.handle(intent, TODAY)
    assert sorted(p["channel"] for p in slack.posts) == ["C1", "C2"]
    action = Intent(
        IntentKind.ACTION,
        GID,
        None,
        {"action": "ASSIGN", "actor": "U1", "accounts": [ACCOUNT], "assignee": "U2", "due": None, "reason": None, "channel": "C1"},
    )
    notifier.handle(action, TODAY)
    assert [u["channel"] for u in slack.updates] == ["C1"]
    assert slack.posts[-1]["channel"] == "C1"


def test_terminal_group_without_notice_is_not_posted():
    _, slack, notifier, intents = _setup(entities=(Entity("db-1", "RESOLVED"),))
    assert intents == []
    notifier.handle(Intent(IntentKind.REFRESH, GID), TODAY)
    assert slack.posts == []


def test_channel_error_falls_back_to_default_channel():
    repo, slack, notifier, intents = _setup(slack=FakeSlack(fail_channels={"C0SHADOW"}))
    notifier.handle(intents[0], TODAY)
    assert slack.posts[0]["channel"] == "C0DEFAULT"
    assert slack.posts[0]["text"].startswith("[라우팅 오류")
    notice = repo.get_notices(GID)[0]
    assert (notice.channel_id, notice.posted_channel_id) == ("C0SHADOW", "C0DEFAULT")


def test_refresh_updates_without_thread():
    _, slack, notifier, intents = _setup()
    notifier.handle(intents[0], TODAY)
    notifier.handle(Intent(IntentKind.REFRESH, GID), TODAY)
    assert len(slack.updates) == 1
    assert len(slack.posts) == 1


def _export_intent(channel="C0SHADOW"):
    return Intent(IntentKind.EXPORT, GID, None, {"channel": channel, "requester": "U7"})


def test_export_uploads_csv_to_requester_dm():
    import csv
    import io

    repo, slack, notifier, intents = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    notifier.handle(_export_intent(), TODAY)
    assert len(slack.files) == 1
    f = slack.files[0]
    assert f["channel"] == "DU7"
    assert f["filename"] == f"AWS_RDS_PLANNED_LIFECYCLE_EVENT_ap-northeast-2_{TODAY:%Y%m%d}.csv"
    rows = list(csv.reader(io.StringIO(f["content"].decode("utf-8-sig"))))[1:]
    assert sorted((r[0], r[6]) for r in rows) == sorted((a, e) for a in (ACCOUNT, OTHER_ACCOUNT) for e in ("db-1", "db-2"))
    assert "계정 2개, 리소스 4개" in f["initial_comment"]
    assert slack.posts == [] and slack.updates == []
    assert repo.get_notices(GID) == []


def test_export_without_items_posts_notice_to_dm():
    _, slack, notifier, _ = _setup(accounts=(ACCOUNT,), router=MapRouter({ACCOUNT: "C1"}))
    notifier.handle(_export_intent("C2"), TODAY)
    assert slack.files == []
    assert [(p["channel"], p["text"]) for p in slack.posts] == [
        ("DU7", "내보낼 리소스가 없습니다: RDS · AWS_RDS_PLANNED_LIFECYCLE_EVENT")
    ]


def _api_error(error="missing_scope"):
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web import SlackResponse

    response = SlackResponse(
        client=None, http_verb="POST", api_url="https://slack.com/api/x", req_args={},
        data={"ok": False, "error": error}, headers={}, status_code=200,
    )
    return SlackApiError("failed", response)


def test_export_open_dm_failure_is_swallowed():
    repo, slack, notifier, _ = _setup()
    slack.open_dm_error = _api_error("cannot_dm_bot")
    notifier.handle(_export_intent(), TODAY)
    assert slack.files == [] and slack.posts == [] and slack.updates == []


def test_export_upload_failure_is_swallowed():
    from slack_sdk.errors import SlackRequestError

    for exc in (_api_error(), SlackRequestError("bad"), __import__("aha.adapters.slack_client", fromlist=["x"]).SlackChannelError("x")):
        _, slack, notifier, _ = _setup()
        slack.upload_error = exc
        notifier.handle(_export_intent(), TODAY)
        assert slack.posts == [] and slack.updates == []


def test_export_retryable_error_propagates():
    import pytest

    from aha.adapters.slack_client import SlackRetryable

    _, slack, notifier, _ = _setup()
    slack.upload_error = SlackRetryable(3)
    with pytest.raises(SlackRetryable):
        notifier.handle(_export_intent(), TODAY)


def test_export_missing_group_still_replies_to_requester():
    slack = FakeSlack()
    notifier = Notifier(InMemoryRepository(), slack, StaticRouter("C0SHADOW"), CFG)
    notifier.handle(_export_intent(), TODAY)
    assert [(p["channel"], p["text"]) for p in slack.posts] == [("DU7", "내보낼 리소스가 없습니다.")]


def test_export_includes_terminal_items():
    import csv
    import io

    repo, slack, notifier, _ = _setup()
    item = repo.list_group_tracking(GID)[0]
    from dataclasses import replace

    from aha.domain.models import State

    repo.save_tracking(replace(item, state=State.RESOLVED_MANUAL))
    notifier.handle(_export_intent(), TODAY)
    rows = list(csv.reader(io.StringIO(slack.files[0]["content"].decode("utf-8-sig"))))[1:]
    assert [r[6] for r in rows] == ["db-1", "db-2"]
    assert rows[0][2] == "RESOLVED_MANUAL"


def _two_channels():
    router = MapRouter({ACCOUNT: "C1", OTHER_ACCOUNT: "C2"})
    repo, slack, notifier, intents = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT), router=router)
    for intent in intents:
        notifier.handle(intent, TODAY)
    return repo, slack, notifier


def test_reminder_touches_only_channels_with_its_accounts():
    _, slack, notifier = _two_channels()
    reminder = Intent(
        IntentKind.REMINDER,
        GID,
        None,
        {"label": "D-30", "accounts": [ACCOUNT], "due": "2026-11-30", "mention": False, "assignees": []},
    )
    notifier.handle(reminder, TODAY)
    assert [u["channel"] for u in slack.updates] == ["C1"]
    thread = slack.posts[-1]
    assert thread["channel"] == "C1"
    assert thread["thread_ts"] == next(p["ts"] for p in slack.posts if p["channel"] == "C1" and p["thread_ts"] is None)
    assert thread["text"] == f"⏰ D-30 · 미조치 계정 1개: {ACCOUNT} (기한 2026-11-30)"


def test_auto_assign_action_touches_only_channels_with_its_accounts():
    _, slack, notifier = _two_channels()
    action = Intent(
        IntentKind.ACTION,
        GID,
        None,
        {"action": "AUTO_ASSIGN", "actor": "system", "assignments": {OTHER_ACCOUNT: "U9"}, "accounts": [OTHER_ACCOUNT], "channel": None},
    )
    notifier.handle(action, TODAY)
    assert [u["channel"] for u in slack.updates] == ["C2"]
    assert slack.posts[-1]["channel"] == "C2"
    assert slack.posts[-1]["text"] == f"계정 이메일 기준으로 담당자를 자동 지정했습니다: {OTHER_ACCOUNT} → <@U9>"
