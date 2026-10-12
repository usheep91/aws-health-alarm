import importlib

import pytest


@pytest.mark.parametrize("module", ["aha.handlers.reconcile", "aha.handlers.notifier", "aha.handlers.interaction"])
def test_handler_modules_import_without_aws_calls(module, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "AHA")
    assert callable(importlib.import_module(module).lambda_handler)
