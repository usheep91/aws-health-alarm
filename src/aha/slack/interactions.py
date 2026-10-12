from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime

from aha.config import Config
from aha.domain.actions import ActionKind, InvalidAction, apply_group_action
from aha.domain.due_date import to_local_date
from aha.domain.models import State, TrackingItem, split_tracking_id
from aha.domain.repository import Repository
from aha.routing import Router, items_for_channel
from aha.slack.views import due_date_view, na_view, resources_view

MENU_ACTIONS = {"resolve": ActionKind.RESOLVE_MANUAL, "triage": ActionKind.TRIAGE_REQUIRED}


def _errors(block_id: str, message: str) -> dict:
    return {"response_action": "errors", "errors": {block_id: message}}


class InteractionService:
    def __init__(self, repo: Repository, slack, router: Router, publisher, cfg: Config, clock: Callable[[], datetime]) -> None:
        self._repo = repo
        self._slack = slack
        self._router = router
        self._publisher = publisher
        self._cfg = cfg
        self._clock = clock

    def handle(self, payload: dict) -> dict | None:
        kind = payload.get("type")
        if kind == "block_actions":
            self._block_action(payload)
            return None
        if kind == "view_submission":
            return self._view_submission(payload)
        return None

    # --- block_actions ---
    def _block_action(self, payload: dict) -> None:
        action = payload["actions"][0]
        action_id = action["action_id"]
        user = payload["user"]["id"]
        trigger = payload.get("trigger_id")
        if action_id == "dashboard":
            return

        if action_id == "account_menu":
            op, tracking_id = action["selected_option"]["value"].split(":", 1)
            group_id, _ = split_tracking_id(tracking_id)
            channel = self._logical_channel(group_id, payload["channel"]["id"])
            if op == "na":
                self._slack.open_view(trigger, na_view({"tracking_ids": [tracking_id], "channel": channel}))
            else:
                self._apply([tracking_id], MENU_ACTIONS[op], user, channel)
            return

        group_id = action["block_id"].removeprefix("grp:")
        channel = self._logical_channel(group_id, payload["channel"]["id"])
        if action_id == "resources":
            self._slack.open_view(trigger, self._resources(group_id, channel))
            return
        items = self._channel_items(group_id, channel, active_only=True)
        if not items:
            return
        tracking_ids = [i.tracking_id for i in items]
        triage_ids = [i.tracking_id for i in items if i.state is State.TRIAGE]
        meta = {"tracking_ids": tracking_ids, "channel": channel}

        if action_id == "assign":
            self._apply(tracking_ids, ActionKind.ASSIGN, user, channel, assignee=action["selected_user"])
        elif action_id == "triage_required":
            self._apply(triage_ids, ActionKind.TRIAGE_REQUIRED, user, channel)
        elif action_id == "triage_na":
            self._slack.open_view(trigger, na_view({**meta, "tracking_ids": triage_ids}))
        elif action_id == "set_due":
            self._slack.open_view(trigger, due_date_view(meta, min(i.due_date for i in items)))

    # --- view_submission ---
    def _view_submission(self, payload: dict) -> dict:
        view = payload["view"]
        meta = json.loads(view["private_metadata"])
        values = view["state"]["values"]
        user = payload["user"]["id"]
        try:
            if view["callback_id"] == "set_due_modal":
                due = date.fromisoformat(values["due"]["value"]["selected_date"])
                if due < to_local_date(self._clock(), self._cfg.timezone):
                    return _errors("due", "오늘 이후 날짜를 선택하세요")
                reason = values["reason"]["value"]["value"]
                self._apply(meta["tracking_ids"], ActionKind.SET_DUE, user, meta["channel"], due=due, reason=reason)
            elif view["callback_id"] == "na_modal":
                reason = values["reason"]["value"]["value"]
                self._apply(meta["tracking_ids"], ActionKind.NOT_APPLICABLE, user, meta["channel"], reason=reason)
        except InvalidAction as e:
            return _errors("reason", str(e))
        return {"response_action": "clear"}

    # --- helpers ---
    def _apply(self, tracking_ids: list[str], kind: ActionKind, actor: str, channel: str, **kwargs) -> None:
        intents = apply_group_action(self._repo, tracking_ids, kind, actor, self._clock(), channel=channel, **kwargs)
        self._publisher.publish(intents)

    def _logical_channel(self, group_id: str, channel: str) -> str:
        """대체 채널(default_channel)에 게시된 Notice의 버튼이면 원래 라우팅 채널로 되돌린다."""
        for notice in self._repo.get_notices(group_id):
            if notice.posted_channel_id == channel:
                return notice.channel_id
        return channel

    def _channel_items(self, group_id: str, channel: str, active_only: bool) -> list[TrackingItem]:
        group = self._repo.get_group(group_id)
        event = self._repo.get_event(group.primary_arn) if group else None
        if event is None:
            return []
        items = items_for_channel(self._repo.list_group_tracking(group_id), channel, self._router, event.personas)
        return [i for i in items if not i.is_terminal] if active_only else items

    def _resources(self, group_id: str, channel: str) -> dict:
        items = self._channel_items(group_id, channel, active_only=False)
        entities = {i.tracking_id: self._repo.list_entities(i.tracking_id) for i in items}
        names = {i.account_id: self._router.account_name(i.account_id) for i in items}
        return resources_view(items, entities, names)
