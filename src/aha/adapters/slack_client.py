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


class SlackGateway:
    def __init__(self, client: WebClient) -> None:
        self._client = client

    def post(self, channel: str, text: str, blocks: list | None = None, thread_ts: str | None = None) -> str:
        return self._call(self._client.chat_postMessage, channel=channel, text=text, blocks=blocks, thread_ts=thread_ts)["ts"]

    def update(self, channel: str, ts: str, text: str, blocks: list) -> None:
        self._call(self._client.chat_update, channel=channel, ts=ts, text=text, blocks=blocks)

    def open_view(self, trigger_id: str, view: dict) -> None:
        self._call(self._client.views_open, trigger_id=trigger_id, view=view)

    @staticmethod
    def _call(fn, **kwargs):
        try:
            return fn(**{k: v for k, v in kwargs.items() if v is not None})
        except SlackApiError as e:
            error = e.response.get("error")
            if e.response.status_code == 429 or error == "ratelimited":
                raise SlackRetryable(int(e.response.headers.get("Retry-After", 1))) from e
            if error in CHANNEL_ERRORS:
                raise SlackChannelError(error) from e
            raise
        except (TimeoutError, ConnectionError, urllib.error.URLError) as e:
            raise SlackRetryable() from e
