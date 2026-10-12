import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

from aha.adapters.slack_client import SlackChannelError, SlackGateway, SlackRetryable


def _error(status, error, headers=None):
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/chat.postMessage",
        req_args={},
        data={"ok": False, "error": error},
        headers=headers or {},
        status_code=status,
    )
    return SlackApiError("failed", response)


class StubClient:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def _call(self, name, kwargs, result):
        self.calls.append((name, kwargs))
        if self.exc:
            raise self.exc
        return result

    def chat_postMessage(self, **kwargs):
        return self._call("post", kwargs, {"ts": "1.0"})

    def chat_update(self, **kwargs):
        return self._call("update", kwargs, {"ok": True})

    def views_open(self, **kwargs):
        return self._call("views_open", kwargs, {"ok": True})


def test_post_drops_none_arguments():
    client = StubClient()
    assert SlackGateway(client).post("C1", "hi") == "1.0"
    assert client.calls == [("post", {"channel": "C1", "text": "hi"})]


def test_rate_limit_is_retryable():
    gateway = SlackGateway(StubClient(_error(429, "ratelimited", {"Retry-After": "7"})))
    with pytest.raises(SlackRetryable) as exc:
        gateway.update("C1", "1.0", "t", [])
    assert exc.value.retry_after == 7


@pytest.mark.parametrize("error", ["channel_not_found", "not_in_channel", "is_archived"])
def test_channel_errors(error):
    with pytest.raises(SlackChannelError):
        SlackGateway(StubClient(_error(200, error))).post("C1", "hi")


def test_other_errors_propagate():
    with pytest.raises(SlackApiError):
        SlackGateway(StubClient(_error(200, "invalid_blocks"))).post("C1", "hi")


def test_server_errors_are_retryable():
    with pytest.raises(SlackRetryable):
        SlackGateway(StubClient(_error(503, "service_unavailable"))).post("C1", "hi")


def test_retry_after_defaults_to_one():
    with pytest.raises(SlackRetryable) as exc:
        SlackGateway(StubClient(_error(429, "ratelimited"))).post("C1", "hi")
    assert exc.value.retry_after == 1


def test_non_integer_retry_after_defaults_to_one():
    with pytest.raises(SlackRetryable) as exc:
        SlackGateway(StubClient(_error(429, "ratelimited", {"Retry-After": "soon"}))).post("C1", "hi")
    assert exc.value.retry_after == 1
