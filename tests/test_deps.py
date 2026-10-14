from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from aha.handlers import deps


def _rate_limit_handlers(gateway):
    return [h for h in gateway._client.retry_handlers if isinstance(h, RateLimitErrorRetryHandler)]


def test_only_reminder_slack_client_retries_rate_limits(monkeypatch):
    monkeypatch.setattr(deps, "slack_secret", lambda: {"bot_token": "xoxb-test"})
    deps.slack.cache_clear()
    deps.reminder_slack.cache_clear()
    try:
        # interaction은 3초 안에 Slack에 응답해야 하므로 Retry-After 대기를 하지 않는다.
        assert _rate_limit_handlers(deps.slack()) == []
        assert [h.max_retry_count for h in _rate_limit_handlers(deps.reminder_slack())] == [2]
        assert deps.reminder_slack() is deps.reminder_slack()
        assert deps.reminder_slack()._client.timeout == deps.slack()._client.timeout == deps.SLACK_TIMEOUT_SECONDS
    finally:
        deps.slack.cache_clear()
        deps.reminder_slack.cache_clear()


def test_org_directory_is_fresh_per_call_but_shares_client():
    deps.organizations_client.cache_clear()
    try:
        first, second = deps.org_directory(), deps.org_directory()
        assert first is not second
        assert first._client is second._client
    finally:
        deps.organizations_client.cache_clear()
