import importlib
import json
from types import SimpleNamespace

import pytest

from aha.reminder import ReminderResult


def _context():
    return SimpleNamespace(
        function_name="reminder",
        memory_limit_in_mb=256,
        invoked_function_arn="arn:aws:lambda:ap-northeast-2:111122223333:function:reminder",
        aws_request_id="req-1",
        get_remaining_time_in_millis=lambda: 300_000,
    )


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "AHA")
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "aha")
    module = importlib.reload(importlib.import_module("aha.handlers.reminder"))
    for name in ("repository", "publisher", "reminder_slack", "org_directory", "config"):
        monkeypatch.setattr(module.deps, name, lambda: None)
    return module


def test_reminder_handler_emits_success_metric_and_logs_result(handler, monkeypatch, capsys, caplog):
    seen = {}

    class FakeService:
        def __init__(self, *args):
            assert len(args) == 5

        def run(self, now, time_left=None):
            seen["time_left"] = time_left()
            return ReminderResult(evaluated=3, reminded=1, auto_assigned=2)

    monkeypatch.setattr(handler, "ReminderService", FakeService)
    returned = handler.lambda_handler({}, _context())
    assert seen["time_left"] == 300.0
    assert returned == {"evaluated": 3, "reminded": 1, "auto_assigned": 2}

    docs = []
    for line in capsys.readouterr().out.splitlines():
        try:
            docs.append(json.loads(line))
        except ValueError:
            continue
    metric_docs = [d for d in docs if "_aws" in d]
    assert len(metric_docs) == 1
    metric = metric_docs[0]
    assert {m["Name"] for m in metric["_aws"]["CloudWatchMetrics"][0]["Metrics"]} == {"ReminderSuccess"}
    # 알람 dimensions {service = "aha"}와 정확히 일치해야 한다.
    assert metric["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["service"]]
    assert metric["service"] == "aha"
    logs = [r for r in caplog.records if r.getMessage() == "reminder finished"]
    assert logs[0].result == {"evaluated": 3, "reminded": 1, "auto_assigned": 2}
