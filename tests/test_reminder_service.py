from datetime import date, timedelta

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

from aha.adapters.org_client import AccountInfo
from aha.adapters.slack_client import SlackRetryable
from aha.domain.models import IntentKind, State, Tier
from aha.domain.reminders import BASELINE
from aha.domain.repository import ConcurrentModification
from aha.reminder import ReminderResult, ReminderService
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, TODAY, make_config, make_item
from tests.fakes import FakeSlack, InMemoryRepository, RecordingPublisher

CFG = make_config()
DAY = timedelta(days=1)
USHEEP = "usheep@amazon.com"


class FakeOrg:
    def __init__(self, accounts=None, error=None):
        self._accounts = accounts if accounts is not None else {
            ACCOUNT: AccountInfo(ACCOUNT, "dev", "Usheep+Sub02@amazon.com", "ACTIVE"),
            OTHER_ACCOUNT: AccountInfo(OTHER_ACCOUNT, "prod", "nobody@amazon.com", "ACTIVE"),
        }
        self.error = error
        self.calls = 0

    def accounts(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self._accounts


def _service(*items, users=None, org=None, repo=None):
    repo = repo or InMemoryRepository()
    for item in items:
        repo.create_tracking(item)
    publisher = RecordingPublisher()
    slack = FakeSlack(users={USHEEP: "UUSHEEP"} if users is None else users)
    return ReminderService(repo, publisher, slack, org or FakeOrg(), CFG), repo, publisher, slack


def _three_items(days_left_on_first_run, **overrides):
    due = TODAY + timedelta(days=days_left_on_first_run)
    return (
        make_item(group_id="g1", account_id=ACCOUNT, due_date=due, **overrides),
        make_item(group_id="g1", account_id=OTHER_ACCOUNT, due_date=due + DAY, **overrides),
        make_item(group_id="g2", account_id=ACCOUNT, due_date=due, **overrides),
    )


def _kinds(publisher):
    return [i.kind for i in publisher.intents]


def test_first_run_baselines_silently_and_auto_assigns():
    service, repo, publisher, slack = _service(*_three_items(49))
    result = service.run(NOW)
    assert result == ReminderResult(evaluated=3, reminded=0, auto_assigned=2)
    assert _kinds(publisher) == [IntentKind.ACTION, IntentKind.ACTION]
    assert [(i.group_id, i.data["action"], i.data["assignments"]) for i in publisher.intents] == [
        ("g1", "AUTO_ASSIGN", {ACCOUNT: "UUSHEEP"}),
        ("g2", "AUTO_ASSIGN", {ACCOUNT: "UUSHEEP"}),
    ]
    # 같은 계정은 실행당 한 번만 조회하고, Slack에 없는 사용자는 미지정으로 남긴다.
    assert sorted(slack.lookups) == ["nobody@amazon.com", USHEEP]
    assert repo.get_tracking(f"g1#{OTHER_ACCOUNT}").assignee is None
    assert repo.get_tracking(f"g1#{ACCOUNT}").reminders_sent == {BASELINE, "D-90", "D-60"}


def test_next_day_milestone_emits_one_reminder_per_group():
    service, _, publisher, _ = _service(*_three_items(31))
    service.run(NOW)
    publisher.intents.clear()
    result = service.run(NOW + DAY)
    assert result == ReminderResult(evaluated=3, reminded=2, auto_assigned=0)
    reminders = publisher.intents
    assert [(i.kind, i.group_id, i.tracking_id) for i in reminders] == [
        (IntentKind.REMINDER, "g1", None),
        (IntentKind.REMINDER, "g2", None),
    ]
    assert reminders[0].data == {
        "label": "D-30",
        "accounts": [ACCOUNT],
        "due": (TODAY + timedelta(days=31)).isoformat(),
        "mention": False,
        "assignees": ["UUSHEEP"],
    }


def test_group_reminder_combines_accounts_with_same_label():
    due = TODAY + timedelta(days=15)
    service, _, publisher, _ = _service(
        make_item(group_id="g1", account_id=ACCOUNT, due_date=due),
        make_item(group_id="g1", account_id=OTHER_ACCOUNT, due_date=due, assignee="UMANUAL"),
    )
    service.run(NOW)
    publisher.intents.clear()
    service.run(NOW + DAY)
    assert len(publisher.intents) == 1
    assert publisher.intents[0].data == {
        "label": "D-14",
        "accounts": [ACCOUNT, OTHER_ACCOUNT],
        "due": due.isoformat(),
        "mention": True,
        "assignees": ["UMANUAL", "UUSHEEP"],
    }


def test_manual_assignee_is_not_overwritten_or_looked_up():
    service, repo, publisher, slack = _service(make_item(assignee="UMANUAL"))
    result = service.run(NOW)
    assert result.auto_assigned == 0
    assert slack.lookups == []
    assert publisher.intents == []
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee == "UMANUAL"


def test_unknown_slack_user_stays_unassigned():
    service, repo, publisher, _ = _service(make_item(), users={})
    assert service.run(NOW).auto_assigned == 0
    assert publisher.intents == []
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee is None


def test_account_missing_from_organization_is_skipped():
    service, repo, _, slack = _service(make_item(), org=FakeOrg(accounts={}))
    service.run(NOW)
    assert slack.lookups == []
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee is None


def test_triage_items_are_auto_assigned_but_not_reminded():
    service, repo, publisher, _ = _service(make_item(state=State.TRIAGE, tier=Tier.REVIEW, due_date=TODAY))
    result = service.run(NOW)
    assert result == ReminderResult(evaluated=0, reminded=0, auto_assigned=1)
    assert _kinds(publisher) == [IntentKind.ACTION]
    assert repo.get_tracking(f"g1#{ACCOUNT}").reminders_sent == set()


def test_same_day_rerun_emits_nothing():
    service, _, publisher, _ = _service(*_three_items(31))
    service.run(NOW)
    service.run(NOW + DAY)
    publisher.intents.clear()
    assert service.run(NOW + DAY) == ReminderResult(evaluated=3, reminded=0, auto_assigned=0)
    assert publisher.intents == []


def test_overdue_reminder_after_baseline():
    service, _, publisher, _ = _service(make_item(due_date=TODAY))
    service.run(NOW)
    publisher.intents.clear()
    service.run(NOW + DAY)
    assert [(i.data["label"], i.data["mention"]) for i in publisher.intents if i.kind is IntentKind.REMINDER] == [("D+1", True)]


def _slack_error(error):
    response = SlackResponse(
        client=None, http_verb="POST", api_url="https://slack.com/api/users.lookupByEmail",
        req_args={}, data={"ok": False, "error": error}, headers={}, status_code=200,
    )
    return SlackApiError("failed", response)


def test_lookup_failure_skips_only_that_account():
    service, repo, _, slack = _service(make_item(account_id=ACCOUNT), make_item(account_id=OTHER_ACCOUNT))
    slack.users["nobody@amazon.com"] = "UNOBODY"
    slack.lookup_errors[USHEEP] = _slack_error("missing_scope")
    assert service.run(NOW).auto_assigned == 1
    assert repo.get_tracking(f"g1#{ACCOUNT}").assignee is None
    assert repo.get_tracking(f"g1#{OTHER_ACCOUNT}").assignee == "UNOBODY"


def test_rate_limited_lookup_stops_lookups_but_still_reminds():
    third = "777788889999"
    org = FakeOrg()
    org._accounts[third] = AccountInfo(third, "stg", "third@amazon.com", "ACTIVE")
    reached = {BASELINE, "D-90", "D-60"}
    service, repo, publisher, slack = _service(
        make_item(group_id="g1", account_id=OTHER_ACCOUNT, due_date=TODAY + timedelta(days=30), reminders_sent=reached),
        make_item(group_id="g2", account_id=ACCOUNT, due_date=TODAY + timedelta(days=31), reminders_sent=reached),
        make_item(group_id="g3", account_id=third, due_date=TODAY + timedelta(days=32), reminders_sent=reached),
        users={"nobody@amazon.com": "UNOBODY", "third@amazon.com": "UTHIRD"},
        org=org,
    )
    slack.lookup_errors[USHEEP] = SlackRetryable(3)
    result = service.run(NOW)
    # 스로틀링 이후에는 이번 실행에서 더 조회하지 않고, 이미 찾은 담당자는 지정한다.
    assert slack.lookups == ["nobody@amazon.com", USHEEP]
    assert result.auto_assigned == 1
    assert repo.get_tracking(f"g1#{OTHER_ACCOUNT}").assignee == "UNOBODY"
    assert repo.get_tracking(f"g3#{third}").assignee is None
    assert [(i.kind, i.group_id) for i in publisher.intents] == [(IntentKind.ACTION, "g1"), (IntentKind.REMINDER, "g1")]


def test_organization_failure_skips_auto_assign_but_still_reminds():
    org = FakeOrg(error=RuntimeError("AccessDenied"))
    service, _, publisher, slack = _service(make_item(due_date=TODAY + timedelta(days=31)), org=org)
    service.run(NOW)
    result = service.run(NOW + DAY)
    assert result.reminded == 1
    assert slack.lookups == []
    assert _kinds(publisher) == [IntentKind.REMINDER]


class ConflictOnceRepository(InMemoryRepository):
    """리마인더 기록 저장 직전에 다른 실행이 항목을 갱신한 상황을 흉내 낸다."""

    def __init__(self, conflicts):
        super().__init__()
        self.conflicts = conflicts

    def save_tracking(self, item):
        if self.conflicts and item.reminders_sent:
            self.conflicts -= 1
            stored = self.tracking[item.tracking_id]
            stored.version += 1
            raise ConcurrentModification(item.tracking_id)
        super().save_tracking(item)


def test_reminder_save_conflict_is_retried():
    repo = ConflictOnceRepository(conflicts=2)
    service, repo, publisher, _ = _service(make_item(assignee="U1", due_date=TODAY), repo=repo)
    service.run(NOW)
    assert repo.get_tracking(f"g1#{ACCOUNT}").reminders_sent >= {BASELINE, "D-0"}


def test_reminder_save_gives_up_after_three_conflicts():
    repo = ConflictOnceRepository(conflicts=3)
    service, repo, _, _ = _service(make_item(assignee="U1", due_date=TODAY), repo=repo)
    assert service.run(NOW).evaluated == 1
    assert repo.get_tracking(f"g1#{ACCOUNT}").reminders_sent == set()


def test_reminder_uses_local_date():
    # 2026-10-12 15:00 UTC = 2026-10-13 00:00 KST → D-30 기준일이 하루 앞선다.
    due = date(2026, 11, 12)
    service, _, publisher, _ = _service(make_item(assignee="U1", due_date=due, reminders_sent={BASELINE, "D-90", "D-60"}))
    service.run(NOW + timedelta(hours=15))
    assert [i.data["label"] for i in publisher.intents] == ["D-30"]
