from __future__ import annotations

import logging
from datetime import date

from slack_sdk.errors import SlackApiError, SlackRequestError

from aha.adapters.slack_client import SlackChannelError
from aha.config import Config
from aha.domain.models import EventRecord, Intent, IntentKind, Notice, TrackingItem
from aha.domain.repository import Repository
from aha.routing import Router, items_for_channel
from aha.slack.export import build_resource_csv, export_filename
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
        if intent.kind is IntentKind.EXPORT:
            self._export_best_effort(intent, event, today)
            return
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

    def _export_best_effort(self, intent: Intent, event: EventRecord | None, today: date) -> None:
        """내보내기는 best-effort: 영구 실패는 로그만 남기고 재시도하지 않는다(SlackRetryable만 전파)."""
        try:
            self._export(intent, event, today)
        except (SlackApiError, SlackRequestError, SlackChannelError) as e:
            logger.warning(
                "csv export failed",
                extra={"group_id": intent.group_id, "requester": intent.data.get("requester"), "error": str(e)},
            )

    def _export(self, intent: Intent, event: EventRecord | None, today: date) -> None:
        if event is None:
            dm = self._slack.open_dm(intent.data["requester"])
            self._slack.post(dm, "내보낼 리소스가 없습니다.")
            return
        items = items_for_channel(
            self._repo.list_group_tracking(intent.group_id), intent.data["channel"], self._router, event.personas
        )
        dm = self._slack.open_dm(intent.data["requester"])
        if not items:
            self._slack.post(dm, f"내보낼 리소스가 없습니다: {event.service} · {event.event_type_code}")
            return
        entities = {i.tracking_id: self._repo.list_entities(i.tracking_id) for i in items}
        names = {i.account_id: self._router.account_name(i.account_id) for i in items}
        total = sum(len(v) for v in entities.values())
        self._slack.upload_file(
            dm,
            export_filename(event, today),
            build_resource_csv(event, items, entities, names),
            title=f"{event.service} · {event.event_type_code} 영향 리소스",
            initial_comment=f"{event.event_type_code} ({event.event_region}) 영향 리소스 목록입니다. 계정 {len(items)}개, 리소스 {total}개.",
        )

    @staticmethod
    def _concerns(intent: Intent, channel: str, items: list[TrackingItem]) -> bool:
        if intent.data.get("channel"):
            return intent.data["channel"] == channel
        if intent.tracking_id:
            return any(i.tracking_id == intent.tracking_id for i in items)
        if intent.data.get("accounts"):
            # 채널 지정이 없는 계정 단위 알림(리마인더, 자동 지정)은 해당 계정이 있는 채널에만 남긴다.
            accounts = set(intent.data["accounts"])
            return any(i.account_id in accounts for i in items)
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
            thread = render_thread(_scoped(intent, items), names)
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


# 계정별 값을 담은 필드. 채널 스레드에는 그 채널의 계정 값만 남긴다.
PER_ACCOUNT_FIELDS = ("assignments", "due_by_account", "assignee_by_account")


def _scoped(intent: Intent, items: list[TrackingItem]) -> Intent:
    """채널 지정 없이 여러 계정을 담은 Intent(리마인더, 자동 지정)를 이 채널의 계정으로 좁힌다.

    다른 채널(팀)의 계정 ID나 담당자 멘션이 스레드에 노출되지 않게 한다.
    """
    d = intent.data
    if intent.tracking_id or d.get("channel") or not d.get("accounts"):
        return intent
    present = {i.account_id for i in items}
    data = {**d, "accounts": [a for a in d["accounts"] if a in present]}
    for key in PER_ACCOUNT_FIELDS:
        if key in d:
            data[key] = {a: v for a, v in d[key].items() if a in present}
    return Intent(intent.kind, intent.group_id, intent.tracking_id, data)
