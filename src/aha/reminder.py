"""매일 09:00 KST 실행: 계정 이메일 기준 담당자 자동 지정 + OPEN 항목 마일스톤 리마인더."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from aha.adapters.org_client import AccountInfo, owner_alias
from aha.adapters.slack_client import SlackRetryable
from aha.config import Config
from aha.domain.actions import auto_assign
from aha.domain.due_date import to_local_date
from aha.domain.models import Intent, IntentKind, State, TrackingItem
from aha.domain.reminders import ReminderDecision, decide_reminder
from aha.domain.repository import ConcurrentModification, Repository

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
AUTO_ASSIGN_STATES = (State.OPEN, State.TRIAGE)
ACTIVE_ACCOUNT = "ACTIVE"


@dataclass
class ReminderResult:
    evaluated: int = 0
    reminded: int = 0
    auto_assigned: int = 0


class ReminderService:
    def __init__(self, repo: Repository, publisher, slack, org, cfg: Config) -> None:
        self._repo = repo
        self._publisher = publisher
        self._slack = slack
        self._org = org
        self._cfg = cfg

    def run(self, now: datetime) -> ReminderResult:
        result = ReminderResult()
        today = to_local_date(now, self._cfg.timezone)
        result.auto_assigned = self._auto_assign(now)

        pending: list[tuple[TrackingItem, ReminderDecision]] = []
        for item in self._repo.list_by_state(State.OPEN):
            result.evaluated += 1
            decision = decide_reminder(item, today, self._cfg)
            if decision.sent != item.reminders_sent:
                pending.append((item, decision))
        labeled = [(item, d.label, d.mention) for item, d in pending if d.label]
        result.reminded = len(labeled)
        # 게시가 실패하면 기록하지 않아 다음 실행이 다시 보낸다. 기록이 실패하면 다음 날 중복될 수 있다(유실보다 낫다).
        self._publisher.publish(_reminder_intents(labeled))
        for item, decision in pending:
            self._record(item, decision, today)
        return result

    # --- 담당자 자동 지정 ---

    def _auto_assign(self, now: datetime) -> int:
        unassigned = [i for state in AUTO_ASSIGN_STATES for i in self._repo.list_by_state(state) if not i.assignee]
        if not unassigned:
            return 0
        try:
            accounts = self._org.accounts()
        except Exception:
            logger.exception("organization account lookup failed; skipping auto-assign")
            return 0
        owners: dict[str, str | None] = {}
        assignments: dict[str, str] = {}
        for item in unassigned:
            if item.account_id not in owners:
                try:
                    owners[item.account_id] = self._resolve_owner(item.account_id, accounts)
                except SlackRetryable as e:
                    # 스로틀링이 계속되면 이번 실행의 조회는 멈추고, 찾은 담당자만 지정한 뒤 리마인더로 넘어간다.
                    logger.warning("slack user lookup rate limited; stopping lookups for this run", extra={"retry_after": e.retry_after})
                    break
            if owners[item.account_id]:
                assignments[item.tracking_id] = owners[item.account_id]
        intents = auto_assign(self._repo, assignments, now)
        self._publisher.publish(intents)
        return sum(len(i.data["accounts"]) for i in intents)

    def _resolve_owner(self, account_id: str, accounts: dict[str, AccountInfo]) -> str | None:
        info = accounts.get(account_id)
        # 정지·해지 진행 중인 계정의 이메일은 더 이상 담당자를 가리키지 않을 수 있다.
        alias = owner_alias(info.email) if info and info.status == ACTIVE_ACCOUNT else None
        if alias is None:
            return None
        try:
            return self._slack.lookup_user_by_email(f"{alias}@{self._cfg.owner_email_domain}")
        except SlackRetryable:
            raise
        except Exception:
            logger.exception("slack user lookup failed", extra={"account_id": account_id})
            return None

    # --- 리마인더 ---

    def _record(self, item: TrackingItem, decision: ReminderDecision, today: date) -> None:
        current = item
        for _ in range(MAX_ATTEMPTS):
            current.reminders_sent = set(decision.sent)
            try:
                self._repo.save_tracking(current)
                return
            except ConcurrentModification:
                fresh = self._repo.get_tracking(item.tracking_id)
                if fresh is None:
                    return
                retry = decide_reminder(fresh, today, self._cfg)
                if retry.sent == fresh.reminders_sent:
                    return
                if retry.label not in (None, decision.label):
                    # 이미 게시한 것과 다른 리마인더가 필요해졌다. 기록하지 않고 다음 실행에 맡긴다.
                    return
                current, decision = fresh, retry
        logger.warning("reminder state save conflicted; will retry next run", extra={"tracking_id": item.tracking_id})

def _reminder_intents(labeled: list[tuple[TrackingItem, str, bool]]) -> list[Intent]:
    grouped: dict[tuple[str, str], list[tuple[TrackingItem, bool]]] = {}
    for item, label, mention in labeled:
        grouped.setdefault((item.group_id, label), []).append((item, mention))
    intents = []
    for (group_id, label), entries in sorted(grouped.items()):
        items = [i for i, _ in entries]
        intents.append(
            Intent(
                IntentKind.REMINDER,
                group_id,
                None,
                {
                    "label": label,
                    "accounts": sorted(i.account_id for i in items),
                    "due_by_account": {i.account_id: i.due_date.isoformat() for i in items},
                    "mention": any(m for _, m in entries),
                    "assignee_by_account": {i.account_id: i.assignee for i in items},
                },
            )
        )
    return intents
