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
from aha.domain.reminders import decide_reminder
from aha.domain.repository import ConcurrentModification, Repository

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
AUTO_ASSIGN_STATES = (State.OPEN, State.TRIAGE)


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

        labeled: list[tuple[TrackingItem, str, bool]] = []
        for item in self._repo.list_by_state(State.OPEN):
            result.evaluated += 1
            saved = self._record_reminder(item, today)
            if saved is not None:
                labeled.append(saved)
        result.reminded = len(labeled)
        self._publisher.publish(_reminder_intents(labeled))
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
                owners[item.account_id] = self._resolve_owner(item.account_id, accounts)
            if owners[item.account_id]:
                assignments[item.tracking_id] = owners[item.account_id]
        intents = auto_assign(self._repo, assignments, now)
        self._publisher.publish(intents)
        return sum(len(i.data["accounts"]) for i in intents)

    def _resolve_owner(self, account_id: str, accounts: dict[str, AccountInfo]) -> str | None:
        info = accounts.get(account_id)
        alias = owner_alias(info.email) if info else None
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

    def _record_reminder(self, item: TrackingItem, today: date) -> tuple[TrackingItem, str, bool] | None:
        """판정 결과를 저장하고, 보낼 리마인더가 있으면 (항목, 라벨, 멘션 여부)를 돌려준다."""
        current: TrackingItem | None = item
        for _ in range(MAX_ATTEMPTS):
            decision = decide_reminder(current, today, self._cfg)
            if decision.sent == current.reminders_sent:
                return None
            current.reminders_sent = set(decision.sent)
            try:
                self._repo.save_tracking(current)
            except ConcurrentModification:
                current = self._repo.get_tracking(item.tracking_id)
                if current is None:
                    return None
                continue
            return (current, decision.label, decision.mention) if decision.label else None
        logger.warning("reminder state save conflicted; will retry next run", extra={"tracking_id": item.tracking_id})
        return None


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
                    "due": min(i.due_date for i in items).isoformat(),
                    "mention": any(m for _, m in entries),
                    "assignees": sorted({i.assignee for i in items if i.assignee}),
                },
            )
        )
    return intents
