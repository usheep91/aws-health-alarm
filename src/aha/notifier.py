from __future__ import annotations

import logging
from datetime import date

from aha.adapters.slack_client import SlackChannelError
from aha.config import Config
from aha.domain.models import EventRecord, Intent, Notice, TrackingItem
from aha.domain.repository import Repository
from aha.routing import Router
from aha.slack.blocks import render_notice, render_thread

logger = logging.getLogger(__name__)


class Notifier:
    """Intent 1건을 받아 해당 Event Group의 채널별 Notice를 게시·수정하고 스레드에 변경을 남긴다."""

    def __init__(self, repo: Repository, slack, router: Router, cfg: Config) -> None:
        self._repo = repo
        self._slack = slack
        self._router = router
        self._cfg = cfg

    def handle(self, intent: Intent, today: date) -> None:
        group = self._repo.get_group(intent.group_id)
        event = self._repo.get_event(group.primary_arn) if group else None
        if event is None:
            logger.warning("group or event missing for intent", extra={"group_id": intent.group_id})
            return
        notices = {n.channel_id: n for n in self._repo.get_notices(intent.group_id)}
        by_channel: dict[str, list[TrackingItem]] = {}
        for item in self._repo.list_group_tracking(intent.group_id):
            for channel in self._router.channels_for(item.account_id, event.personas):
                by_channel.setdefault(channel, []).append(item)
        for channel, items in by_channel.items():
            if self._concerns(intent, channel, items):
                self._sync(intent, event, channel, items, notices.get(channel), today)

    @staticmethod
    def _concerns(intent: Intent, channel: str, items: list[TrackingItem]) -> bool:
        if intent.data.get("channel"):
            return intent.data["channel"] == channel
        if intent.tracking_id:
            return any(i.tracking_id == intent.tracking_id for i in items)
        return True

    def _sync(self, intent, event: EventRecord, channel: str, items, notice: Notice | None, today: date) -> None:
        names = {i.account_id: self._router.account_name(i.account_id) for i in items}
        text, blocks = render_notice(event, items, names, self._cfg, today)
        if notice is None:
            if all(i.is_terminal for i in items):
                return
            posted_channel, ts = self._post_new(channel, text, blocks)
            self._repo.put_notice(Notice(event.group_id, channel, ts, posted_channel))
            return
        try:
            self._slack.update(notice.posted_channel_id, notice.ts, text, blocks)
            thread = render_thread(intent, names)
            if thread:
                self._slack.post(notice.posted_channel_id, thread, thread_ts=notice.ts)
        except SlackChannelError:
            logger.warning("notice channel unavailable", extra={"channel": notice.posted_channel_id, "group_id": event.group_id})

    def _post_new(self, channel: str, text: str, blocks: list) -> tuple[str, str]:
        try:
            return channel, self._slack.post(channel, text, blocks=blocks)
        except SlackChannelError:
            fallback = self._cfg.default_channel
            if fallback == channel:
                raise
            logger.warning("routing channel unavailable, using default", extra={"channel": channel})
            return fallback, self._slack.post(fallback, f"[라우팅 오류: <#{channel}> 게시 실패] {text}", blocks=blocks)
