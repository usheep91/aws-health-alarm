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
