from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from aha.handlers import deps


def test_slack_client_retries_rate_limits(monkeypatch):
    monkeypatch.setattr(deps, "slack_secret", lambda: {"bot_token": "xoxb-test"})
    deps.slack.cache_clear()
    try:
        handlers = [h for h in deps.slack()._client.retry_handlers if isinstance(h, RateLimitErrorRetryHandler)]
    finally:
        deps.slack.cache_clear()
    assert [h.max_retry_count for h in handlers] == [2]
