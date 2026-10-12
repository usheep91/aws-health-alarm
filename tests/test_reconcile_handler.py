import importlib
import json
from types import SimpleNamespace

import pytest

from aha.reconcile import ReconcileResult


def _context(remaining_ms):
    return SimpleNamespace(
        function_name="reconcile",
        memory_limit_in_mb=256,
        invoked_function_arn="arn:aws:lambda:ap-northeast-2:111122223333:function:reconcile",
        aws_request_id="req-1",
        get_remaining_time_in_millis=lambda: remaining_ms,
    )


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "AHA")
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "aha")
    # Lambda 환경과 같이 서비스명 환경변수가 설정된 상태로 Metrics를 생성한다.
    module = importlib.reload(importlib.import_module("aha.handlers.reconcile"))
    for name in ("health", "repository", "publisher", "router", "config"):
        monkeypatch.setattr(module.deps, name, lambda: None)
    return module


def _run(handler, monkeypatch, capsys, mode, result, remaining_ms=900_000):
    seen = {}

    class FakeReconciler:
        def __init__(self, *args):
            pass

        def run(self, mode, now, time_left=None):
            seen["time_left"] = time_left()
            return result

    monkeypatch.setattr(handler, "Reconciler", FakeReconciler)
    returned = handler.lambda_handler({"mode": mode}, _context(remaining_ms))
    metric_names = set()
    for line in capsys.readouterr().out.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        for directive in doc.get("_aws", {}).get("CloudWatchMetrics", []):
            metric_names |= {m["Name"] for m in directive["Metrics"]}
    return returned, metric_names, seen


def test_completed_full_run_emits_success_and_missed(handler, monkeypatch, capsys):
    returned, names, seen = _run(handler, monkeypatch, capsys, "full", ReconcileResult(events=1, created=2))
    assert seen["time_left"] == 900.0
    assert returned["completed"] is True
    assert names == {"ReconcileSuccess", "ReconcileMissedItems"}


def test_incomplete_run_emits_incomplete_only(handler, monkeypatch, capsys):
    returned, names, _ = _run(handler, monkeypatch, capsys, "full", ReconcileResult(completed=False))
    assert returned["completed"] is False
    assert names == {"ReconcileIncomplete"}


def test_completed_event_run_does_not_emit_missed(handler, monkeypatch, capsys):
    _, names, _ = _run(handler, monkeypatch, capsys, "event", ReconcileResult())
    assert names == {"ReconcileSuccess"}


@pytest.mark.parametrize("mode", ["full", "entity"])
def test_incomplete_metric_carries_service_and_mode_dimensions(handler, monkeypatch, capsys, mode):
    class FakeReconciler:
        def __init__(self, *args):
            pass

        def run(self, mode, now, time_left=None):
            return ReconcileResult(completed=False)

    monkeypatch.setattr(handler, "Reconciler", FakeReconciler)
    handler.lambda_handler({"mode": mode}, _context(1000))
    docs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"ReconcileIncomplete"' in line]
    assert len(docs) == 1
    doc = docs[0]
    assert doc["mode"] == mode
    assert doc["service"] == "aha"
    assert ["mode", "service"] == sorted(doc["_aws"]["CloudWatchMetrics"][0]["Dimensions"][0])
