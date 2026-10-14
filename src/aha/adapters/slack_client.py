from __future__ import annotations

import urllib.error

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

CHANNEL_ERRORS = frozenset({"channel_not_found", "not_in_channel", "is_archived"})


class SlackRetryable(Exception):
    def __init__(self, retry_after: int = 0) -> None:
        super().__init__(f"slack retry after {retry_after}s")
        self.retry_after = retry_after


class SlackChannelError(Exception):
    """대상 채널에 게시할 수 없음 (라우팅 설정 오류)."""


def _retry_after(headers) -> int:
    try:
        return int((headers or {}).get("Retry-After", 1))
    except (TypeError, ValueError):
        return 1


class SlackGateway:
    def __init__(self, client: WebClient) -> None:
        self._client = client

    def post(self, channel: str, text: str, blocks: list | None = None, thread_ts: str | None = None) -> str:
        return self._call(self._client.chat_postMessage, channel=channel, text=text, blocks=blocks, thread_ts=thread_ts)["ts"]

    def update(self, channel: str, ts: str, text: str, blocks: list) -> None:
        self._call(self._client.chat_update, channel=channel, ts=ts, text=text, blocks=blocks)

    def open_view(self, trigger_id: str, view: dict) -> None:
        self._call(self._client.views_open, trigger_id=trigger_id, view=view)

    def open_dm(self, user_id: str) -> str:
        return self._call(self._client.conversations_open, users=user_id)["channel"]["id"]

    def upload_file(self, channel: str, filename: str, content: bytes, title: str, initial_comment: str) -> None:
        self._call(
            self._client.files_upload_v2,
            channel=channel,
            filename=filename,
            content=content,
            title=title,
            initial_comment=initial_comment,
        )

    def lookup_user_by_email(self, email: str) -> str | None:
        try:
            return self._call(self._client.users_lookupByEmail, email=email)["user"]["id"]
        except SlackApiError as e:
            if e.response.get("error") == "users_not_found":
                return None
            raise

    @staticmethod
    def _call(fn, **kwargs):
        try:
            return fn(**{k: v for k, v in kwargs.items() if v is not None})
        except SlackApiError as e:
            error = e.response.get("error")
            status = e.response.status_code or 0
            if status == 429 or status >= 500 or error == "ratelimited":
                raise SlackRetryable(_retry_after(e.response.headers)) from e
            if error in CHANNEL_ERRORS:
                raise SlackChannelError(error) from e
            raise
        except (TimeoutError, ConnectionError, urllib.error.URLError) as e:
            raise SlackRetryable() from e
