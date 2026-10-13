import pytest


@pytest.fixture(autouse=True)
def aws_test_env(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "ap-northeast-2",
    }.items():
        monkeypatch.setenv(key, value)
